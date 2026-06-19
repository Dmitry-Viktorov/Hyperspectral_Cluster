"""Unsupervised spectral clustering of AVIRIS-NG hyperspectral cube.

Полный пайплайн неконтролируемой спектральной сегментации:
  1. Чтение гиперспектрального куба ENVI (переиспользование io_envi.py).
  2. Маскирование NoData, фильтрация полос водяного поглощения.
  3. PCA-снижение размерности (IncrementalPCA).
  4. Кластеризация: MiniBatchKMeans (базовый) + GaussianMixture (современный).
  5. Оценка качества: Silhouette, Davies-Bouldin, elbow.
  6. Построение средних спектров кластеров.
  7. Кросс-референс с индексами (NDVI, NDWI, MNDWI, NDBI) для интерпретации.
  8. Сохранение GeoTIFF, PNG, CSV, легенд.

Usage:
    python scripts/cluster_segmentation.py
    python scripts/cluster_segmentation.py --n-clusters 10
    python scripts/cluster_segmentation.py --method both --n-clusters 8
    python scripts/cluster_segmentation.py --no-viz
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# --- Путь к модулям HyperSpectral_Index ---
SCRIPTS_DIR = Path(__file__).resolve().parent
INDEX_SCRIPTS = (SCRIPTS_DIR.parent.parent / "HyperSpectral_Index" / "scripts").resolve()
if str(INDEX_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(INDEX_SCRIPTS))

from io_envi import open_envi_cube, find_nearest_band, write_tiff
from indices import INDEX_DEFINITIONS, INDEX_BY_NAME, compute_normalized_difference

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import ListedColormap

# --- Константы ---
ROOT = SCRIPTS_DIR.parent
INDEX_ROOT = ROOT.parent / "HyperSpectral_Index"
DEFAULT_DATASET = INDEX_ROOT / "dataset" / "ang20160831t201002_rfl_v1n2"
DEFAULT_RESULTS = ROOT / "results"

# Диапазоны водяного поглощения (нм) — полосы с шумом
WATER_ABSORPTION_NM: tuple[tuple[float, float], ...] = (
    (1350.0, 1450.0),
    (1800.0, 1950.0),
)

# Число кластеров по умолчанию
DEFAULT_N_CLUSTERS = 8
# Максимум пикселей для обучения (выборка)
MAX_TRAIN_PIXELS = 300_000
# Число компонент PCA по умолчанию
DEFAULT_PCA_COMPONENTS = 30
# Seed для воспроизводимости
RANDOM_SEED = 42
# Шаг по полосам (1 = все, 3 = каждая 3-я — компромисс скорость/качество)
BAND_STEP = 3

# Палитра для кластеров (до 15 цветов, различимых)
CLUSTER_PALETTE = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00",
    "#ffff33", "#a65628", "#f781bf", "#66c2a5", "#fc8d62",
    "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f", "#e5c494",
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Unsupervised spectral clustering of AVIRIS-NG hyperspectral cube"
    )
    p.add_argument("--input", type=Path, default=DEFAULT_DATASET,
                   help="Path to extracted AVIRIS ENVI folder or *_img file")
    p.add_argument("--output-root", type=Path, default=DEFAULT_RESULTS)
    p.add_argument("--method", type=str, default="both", choices=["kmeans", "gmm", "both"],
                   help="Clustering method(s) to apply")
    p.add_argument("--n-clusters", type=int, default=DEFAULT_N_CLUSTERS,
                   help="Number of clusters K")
    p.add_argument("--pca-components", type=int, default=DEFAULT_PCA_COMPONENTS,
                   help="Number of PCA components")
    p.add_argument("--max-train-pixels", type=int, default=MAX_TRAIN_PIXELS,
                   help="Maximum pixels for training")
    p.add_argument("--no-viz", action="store_true", help="Skip PNG visualizations")
    p.add_argument("--no-tiff", action="store_true", help="Skip GeoTIFF output")
    p.add_argument("--sample-step", type=int, default=1,
                   help="Spatial subsampling step (1=full resolution)")
    p.add_argument("--spatial-step", type=int, default=1,
                   help="Spatial step for full-image PCA transform (1=all pixels, 2=every 2nd)")
    p.add_argument("--band-step", type=int, default=BAND_STEP,
                   help=f"Spectral band subsampling step (default={BAND_STEP}, 1=all bands)")
    return p.parse_args()


# ===================================================================
# 1. Подготовка данных
# ===================================================================

def _good_band_indices(wavelengths_nm: np.ndarray, step: int = BAND_STEP) -> list[int]:
    """Вернуть индексы полос, исключая водяное поглощение."""
    good = []
    for i in range(0, len(wavelengths_nm), step):
        wl = wavelengths_nm[i]
        if not any(lo <= wl <= hi for lo, hi in WATER_ABSORPTION_NM):
            good.append(i)
    return good


def _read_chunk(ds, band_indices: list[int], y0: int, chunk_lines: int, info) -> np.ndarray:
    """Читать чанк строк для выбранных полос (BIL-оптимизированно).

    Для BIL-формата используется прямой numpy memmap вместо GDAL ReadAsArray,
    что даёт мгновенный доступ к данным без накладных расходов GDAL.

    Returns:
        Массив (chunk_lines, samples, len(band_indices)) float32, NoData → NaN.
    """
    # Используем numpy memmap к сырому ENVI файлу (BIL: lines × bands × samples)
    img_path = str(info.image_path)
    if not hasattr(_read_chunk, '_memmap_cache'):
        _read_chunk._memmap_cache = {}
    cache_key = img_path
    if cache_key not in _read_chunk._memmap_cache:
        raw = np.memmap(
            img_path, dtype=np.float32, mode='r',
            shape=(info.lines, info.bands, info.samples),
        )
        _read_chunk._memmap_cache[cache_key] = raw
    else:
        raw = _read_chunk._memmap_cache[cache_key]

    # BIL layout: (lines, bands, samples)
    # Читаем нужные строки и полосы — без .copy(), только view для ленивого доступа
    # Транспонируем в (h, samples, n_bands) — это всё ещё view над memmap
    chunk = raw[y0:y0 + chunk_lines, :, :]          # (h, 425, 700)
    chunk = chunk[:, band_indices, :]                 # (h, n_bands, 700)
    chunk = np.transpose(chunk, (0, 2, 1))            # (h, 700, n_bands) — view

    # Применяем scale_factor без копирования всего чанка
    if info.nodata_value is not None and info.scale_factor == 1.0:
        pass  # будем обрабатывать при копировании
    elif info.scale_factor != 1.0:
        chunk = (chunk * info.scale_factor).copy()  # forced copy при умножении
    elif info.nodata_value is not None:
        chunk = chunk.copy()  # forced copy для маскирования
    else:
        chunk = chunk.copy()  # always copy to make contiguous

    if info.nodata_value is not None:
        chunk[chunk == float(info.nodata_value)] = np.nan
    chunk[~np.isfinite(chunk)] = np.nan
    return chunk


def build_valid_mask(cube_reader, band_indices: list[int]) -> np.ndarray:
    """Построить бинарную маску валидных пикселей (True = все ключевые полосы валидны).

    Оптимизация: проверяет только 3 репрезентативные полосы (начало/середина/конец
    спектрального диапазона), поскольку NoData (−9999 → NaN) глобален для ENVI.
    Использует BIL-эффективное чанковое чтение.
    """
    info = cube_reader.scene_info
    ds = cube_reader.dataset
    # Берём 3 полосы из разных частей спектра
    check_indices = [
        band_indices[0],
        band_indices[len(band_indices) // 2],
        band_indices[-1],
    ]
    CHUNK = 500
    mask = np.ones((info.lines, info.samples), dtype=bool)
    for y0 in range(0, info.lines, CHUNK):
        h = min(CHUNK, info.lines - y0)
        chunk = _read_chunk(ds, check_indices, y0, h, info)
        # chunk shape: (h, samples, 3)
        chunk_valid = np.all(np.isfinite(chunk), axis=2)
        mask[y0:y0 + h] = chunk_valid
    return mask


def extract_training_data(
    cube_reader,
    band_indices: list[int],
    valid_mask: np.ndarray,
    max_pixels: int = MAX_TRAIN_PIXELS,
    seed: int = RANDOM_SEED,
) -> np.ndarray:
    """Извлечь выборку пикселей для обучения кластеризаторов (memmap-оптимизированно).

    Сортируем пиксели по строкам, читаем каждую нужную строку из memmap
    один раз, извлекая все сэмплированные пиксели в этой строке.

    Returns:
        Массив (n_train, n_bands) float32.
    """
    info = cube_reader.scene_info
    img_path = str(info.image_path)

    rows_all, cols_all = np.where(valid_mask)
    n_valid = len(rows_all)
    if n_valid > max_pixels:
        rng = np.random.default_rng(seed)
        idx = rng.choice(n_valid, max_pixels, replace=False)
        rows_all, cols_all = rows_all[idx], cols_all[idx]
    n_used = len(rows_all)
    n_bands = len(band_indices)

    print(f"   Извлечение {n_used:,} пикселей × {n_bands} полос (memmap)...", flush=True)

    # Открываем memmap один раз
    raw = np.memmap(
        img_path, dtype=np.float32, mode='r',
        shape=(info.lines, info.bands, info.samples),
    )  # BIL: (lines, bands, samples)

    # Сортируем пиксели по строкам для последовательного доступа
    sort_idx = np.argsort(rows_all)
    rows_sorted = rows_all[sort_idx]
    cols_sorted = cols_all[sort_idx]

    data = np.empty((n_used, n_bands), dtype=np.float32)
    out_pos = 0

    # Группируем пиксели по строкам и читаем каждую строку только один раз
    from collections import defaultdict
    row_to_cols = defaultdict(list)
    row_to_indices = defaultdict(list)
    for idx in range(n_used):
        r = rows_sorted[idx]
        c = cols_sorted[idx]
        row_to_cols[r].append(c)
        row_to_indices[r].append(idx)

    for r, cols_list in row_to_cols.items():
        # Читаем строку из memmap
        row_data = np.asarray(raw[r, :, :], dtype=np.float32)        # (425, 700)
        row_data = row_data[band_indices, :]                           # (n_bands, 700)
        row_data = np.asarray(row_data.T, dtype=np.float32)           # (700, n_bands)

        if info.nodata_value is not None:
            row_data[row_data == float(info.nodata_value)] = np.nan
        if info.scale_factor != 1.0:
            row_data = row_data * info.scale_factor
        row_data[~np.isfinite(row_data)] = np.nan

        # Извлекаем все пиксели этой строки
        for col, orig_idx in zip(cols_list, row_to_indices[r]):
            data[orig_idx] = row_data[col]
        prev_milestone = (out_pos // 20000) * 20000
        out_pos += len(cols_list)
        new_milestone = (out_pos // 20000) * 20000
        if new_milestone > prev_milestone:
            print(f"   ... извлечено {new_milestone:,}/{n_used:,}", flush=True)

    print(f"   ✓ Данные извлечены: {data.shape}")
    return data


# ===================================================================
# 2. PCA
# ===================================================================

def apply_pca(train_data: np.ndarray, n_components: int):
    """Обучить PCA на тренировочных данных."""
    from sklearn.decomposition import PCA
    print(f"\n🔬 PCA: {train_data.shape[1]} полос → {n_components} компонент...")
    pca = PCA(n_components=n_components, random_state=RANDOM_SEED)
    reduced = pca.fit_transform(train_data)
    explained = pca.explained_variance_ratio_.sum()
    print(f"   Объяснённая дисперсия: {explained:.4f} ({explained * 100:.1f}%)")
    return pca, reduced


def transform_full_image_pca(
    cube_reader,
    band_indices: list[int],
    valid_mask: np.ndarray,
    pca,
    spatial_step: int = 1,
) -> np.ndarray:
    """Применить PCA ко всем валидным пикселям полного изображения (memmap-оптимизированно).

    Читает куб построчно через memmap, для каждой строки извлекает валидные
    пиксели и применяет PCA-трансформацию.

    Args:
        spatial_step: шаг пространственной субдискретизации (1=все, 2=каждый 2-й).
    """
    info = cube_reader.scene_info
    img_path = str(info.image_path)
    n_bands = len(band_indices)
    n_comp = pca.n_components_

    if spatial_step > 1:
        sub_mask = np.zeros_like(valid_mask, dtype=bool)
        sub_mask[::spatial_step, ::spatial_step] = True
        valid_mask = valid_mask & sub_mask

    total_valid = int(np.sum(valid_mask))
    result = np.empty((total_valid, n_comp), dtype=np.float32)
    print(f"   PCA-трансформация ~{total_valid:,} пикселей (memmap)...", flush=True)

    raw = np.memmap(
        img_path, dtype=np.float32, mode='r',
        shape=(info.lines, info.bands, info.samples),
    )

    out_pos = 0
    for r in range(info.lines):
        if not np.any(valid_mask[r]):
            continue

        # Читаем одну строку: все полосы → выбираем нужные → транспонируем
        row_data = raw[r, :, :]  # (425, 700)
        row_data = row_data[band_indices, :]  # (n_bands, 700)
        row_data = np.asarray(row_data.T, dtype=np.float32)  # (700, n_bands)

        if info.nodata_value is not None:
            row_data[row_data == float(info.nodata_value)] = np.nan
        if info.scale_factor != 1.0:
            row_data = row_data * info.scale_factor
        row_data[~np.isfinite(row_data)] = np.nan

        # Извлекаем валидные пиксели из строки
        row_valid = valid_mask[r]
        row_pixels = row_data[row_valid]  # (n_valid_in_row, n_bands)

        if len(row_pixels) > 0:
            reduced = pca.transform(row_pixels).astype(np.float32)
            result[out_pos:out_pos + len(reduced)] = reduced
            out_pos += len(reduced)

        if r % 500 == 0 and r > 0:
            print(f"   ... обработано {r}/{info.lines} строк "
                  f"({r / info.lines * 100:.0f}%)", flush=True)

    print(f"   ✓ PCA завершён: {out_pos:,} пикселей")
    return result[:out_pos]


# ===================================================================
# 3. Кластеризация
# ===================================================================

def _percentile_stretch(band: np.ndarray, lo: float = 2.0, hi: float = 98.0) -> np.ndarray:
    """Процентильная растяжка одного канала в [0, 1]."""
    valid = band[np.isfinite(band)]
    if len(valid) == 0:
        return np.zeros_like(band)
    vlo = float(np.percentile(valid, lo))
    vhi = float(np.percentile(valid, hi))
    stretched = np.clip((band - vlo) / max(vhi - vlo, 1e-9), 0, 1)
    stretched[~np.isfinite(stretched)] = 0
    return stretched

def fit_kmeans(data_reduced: np.ndarray, n_clusters: int, seed: int = RANDOM_SEED):
    """Обучение MiniBatchKMeans с L2-нормализацией (косинусное расстояние)."""
    from sklearn.cluster import MiniBatchKMeans
    from sklearn.preprocessing import normalize
    print(f"\n🟦 K-Means (MiniBatch, K={n_clusters}, cosine distance)...")
    # L2-нормализация: акцент на форме спектра, не на яркости
    data_norm = normalize(data_reduced.astype(np.float64), norm='l2')
    km = MiniBatchKMeans(
        n_clusters=n_clusters,
        random_state=seed,
        batch_size=10_000,
        max_iter=100,
        n_init=3,
        reassignment_ratio=0.01,
    )
    labels = km.fit_predict(data_norm)
    print(f"   Inertia: {km.inertia_:.2f}")
    return km, labels, data_norm


def fit_gmm(data_reduced: np.ndarray, n_clusters: int, seed: int = RANDOM_SEED):
    """Обучение Gaussian Mixture Model с L2-нормализацией."""
    from sklearn.mixture import GaussianMixture
    from sklearn.preprocessing import normalize
    print(f"\n🟩 Gaussian Mixture Model (K={n_clusters}, cosine distance)...")
    data_norm = normalize(data_reduced.astype(np.float64), norm='l2')
    gmm = GaussianMixture(
        n_components=n_clusters,
        covariance_type="diag",
        random_state=seed,
        max_iter=200,
        n_init=3,
        reg_covar=1e-4,
        init_params="k-means++",
    )
    gmm.fit(data_norm)
    labels = gmm.predict(data_norm)
    print(f"   Log-likelihood: {gmm.score(data_norm):.2f}")
    return gmm, labels, data_norm


def predict_full_kmeans(model, data_reduced_full: np.ndarray, batch_size: int = 100_000) -> np.ndarray:
    """Применить K-Means ко всем пикселям."""
    n = data_reduced_full.shape[0]
    labels = np.empty(n, dtype=np.int32)
    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        labels[start:end] = model.predict(data_reduced_full[start:end])
    return labels


def predict_full_gmm(model, data_reduced_full: np.ndarray, batch_size: int = 100_000):
    """Применить GMM ко всем пикселям. Возвращает hard labels и вероятности."""
    n = data_reduced_full.shape[0]
    labels = np.empty(n, dtype=np.int32)
    probs = np.empty((n, model.n_components), dtype=np.float32)
    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        chunk_probs = model.predict_proba(data_reduced_full[start:end])
        probs[start:end] = chunk_probs.astype(np.float32)
        labels[start:end] = chunk_probs.argmax(axis=1).astype(np.int32)
    return labels, probs


# ===================================================================
# 4. Оценка качества
# ===================================================================

def evaluate_clustering(data_reduced: np.ndarray, labels: np.ndarray) -> dict:
    """Вычислить метрики качества кластеризации."""
    from sklearn.metrics import silhouette_score, davies_bouldin_score

    metrics = {}
    # Silhouette — на подвыборке для скорости
    n = data_reduced.shape[0]
    if n > 10_000:
        rng = np.random.default_rng(RANDOM_SEED)
        idx = rng.choice(n, 10_000, replace=False)
        sample_data = data_reduced[idx]
        sample_labels = labels[idx]
    else:
        sample_data = data_reduced
        sample_labels = labels

    try:
        metrics["silhouette"] = silhouette_score(sample_data, sample_labels)
    except Exception:
        metrics["silhouette"] = float("nan")

    try:
        metrics["davies_bouldin"] = davies_bouldin_score(sample_data, sample_labels)
    except Exception:
        metrics["davies_bouldin"] = float("nan")

    # Inertia (WCSS) — только если labels от K-Means
    from sklearn.metrics import pairwise_distances_argmin_min
    try:
        from sklearn.cluster import KMeans
        # Приблизительная inertia через центроиды
        unique_labels = np.unique(labels)
        centroids = np.array([sample_data[sample_labels == lbl].mean(axis=0) for lbl in unique_labels])
        _, dists = pairwise_distances_argmin_min(sample_data, centroids)
        metrics["approx_inertia"] = float((dists ** 2).sum())
    except Exception:
        metrics["approx_inertia"] = float("nan")

    return metrics


def compute_elbow_scores(data_reduced: np.ndarray, k_range: range, seed: int = RANDOM_SEED) -> list[dict]:
    """Вычислить метрики для диапазона K."""
    from sklearn.cluster import MiniBatchKMeans
    from sklearn.metrics import silhouette_score, davies_bouldin_score

    results = []
    print(f"\n📐 Elbow analysis: K ∈ {k_range}...")
    for k in k_range:
        km = MiniBatchKMeans(n_clusters=k, random_state=seed, batch_size=10_000, max_iter=100, n_init=3)
        lbls = km.fit_predict(data_reduced)

        # Silhouette на подвыборке
        n = data_reduced.shape[0]
        if n > 5_000:
            rng = np.random.default_rng(seed)
            idx = rng.choice(n, 5_000, replace=False)
            s_data, s_lbls = data_reduced[idx], lbls[idx]
        else:
            s_data, s_lbls = data_reduced, lbls

        try:
            sil = silhouette_score(s_data, s_lbls)
        except Exception:
            sil = float("nan")
        try:
            db = davies_bouldin_score(s_data, s_lbls)
        except Exception:
            db = float("nan")

        results.append({
            "k": k, "inertia": km.inertia_,
            "silhouette": sil, "davies_bouldin": db,
        })
        print(f"   K={k:2d}: inertia={km.inertia_:,.0f}  sil={sil:.4f}  DB={db:.4f}")

    return results


# ===================================================================
# 5. Интерпретация: кросс-референс с индексами
# ===================================================================

def compute_cross_indices(
    cube_reader,
    label_map_full: np.ndarray,
    n_clusters: int,
) -> pd.DataFrame:
    """Для каждого кластера вычислить средние значения спектральных индексов (memmap)."""
    info = cube_reader.scene_info
    img_path = str(info.image_path)
    raw = np.memmap(img_path, dtype=np.float32, mode='r',
                    shape=(info.lines, info.bands, info.samples))

    # Вычисляем индексы через memmap (избегаем GDAL read_band)
    index_maps = {}
    for defn in INDEX_DEFINITIONS:
        idx_a, actual_a = find_nearest_band(info.wavelengths_nm, defn.wavelength_a_nm)
        idx_b, actual_b = find_nearest_band(info.wavelengths_nm, defn.wavelength_b_nm)
        # Читаем полосы из memmap: raw[:, band, :] → (lines, samples)
        band_a = np.array(raw[:, idx_a, :], dtype=np.float32, copy=True)
        band_b = np.array(raw[:, idx_b, :], dtype=np.float32, copy=True)
        if info.nodata_value is not None:
            band_a[band_a == float(info.nodata_value)] = np.nan
            band_b[band_b == float(info.nodata_value)] = np.nan
        if info.scale_factor != 1.0:
            band_a = band_a * info.scale_factor
            band_b = band_b * info.scale_factor
        nd_min_sum = 0.003 if defn.name == "NDWI" else 0.01
        index_maps[defn.name] = compute_normalized_difference(band_a, band_b, min_sum=nd_min_sum)
        del band_a, band_b  # освобождаем память

    # Сбор статистик по кластерам
    rows = []
    total_valid = int(np.sum(label_map_full >= 0))

    # Средняя яркость из memmap (используем полосы ВНЕ водяного поглощения)
    # Берём VIS (550 нм), NIR (860 нм), SWIR-1 (1240 нм) — избегаем 1450/1900 нм
    idx_vis, _ = find_nearest_band(info.wavelengths_nm, 550.0)
    idx_nir, _ = find_nearest_band(info.wavelengths_nm, 860.0)
    idx_swir, _ = find_nearest_band(info.wavelengths_nm, 1240.0)
    bright_indices = [idx_vis, idx_nir, idx_swir]
    bright_bands = np.array(raw[:, bright_indices, :], dtype=np.float32, copy=True)
    if info.nodata_value is not None:
        bright_bands[bright_bands == float(info.nodata_value)] = np.nan
    if info.scale_factor != 1.0:
        bright_bands = bright_bands * info.scale_factor
    bright_mean = np.nanmean(bright_bands, axis=1)  # (lines, samples)

    for c in range(n_clusters):
        mask = label_map_full == c
        n_pix = int(np.sum(mask))
        if n_pix == 0:
            continue

        row = {"cluster_id": c, "pixel_count": n_pix,
               "pixel_fraction": n_pix / max(total_valid, 1)}

        for name, imap in index_maps.items():
            vals = imap[mask]
            vals = vals[np.isfinite(vals)]
            row[f"mean_{name.lower()}"] = float(np.mean(vals)) if len(vals) > 0 else float("nan")

        bv = bright_mean[mask]
        row["mean_brightness"] = float(np.nanmean(bv)) if np.any(np.isfinite(bv)) else float("nan")

        rows.append(row)

    df = pd.DataFrame(rows)

    # Доминирующий класс по комбинации индексов (порядок важен!)
    def _dominant(r):
        ndvi = r.get("mean_ndvi", np.nan)
        mndwi = r.get("mean_mndwi", np.nan)
        ndbi = r.get("mean_ndbi", np.nan)
        ndwi_val = r.get("mean_ndwi", np.nan)
        n_pix = r.get("pixel_count", 0)

        if not np.isfinite(ndvi):
            return "не определено"
        # ВОДА: отрицательный NDVI + высокий MNDWI (оба условия!)
        if ndvi < 0.0 and mndwi > 0.3:
            return "открытая вода"
        # ВОДА вариант 2: очень высокий MNDWI при низком NDVI
        if mndwi > 0.6:
            return "открытая вода"
        # ЗАСТРОЙКА: положительный NDBI при низком NDVI
        if ndbi > 0.05 and ndvi < 0.25:
            return "застройка / искусств. покрытия"
        # ГУСТАЯ РАСТИТЕЛЬНОСТЬ
        if ndvi > 0.55:
            return "густая растительность"
        # УМЕРЕННАЯ РАСТИТЕЛЬНОСТЬ
        if ndvi > 0.30:
            return "умеренная растительность"
        # РАЗРЕЖЕННАЯ РАСТИТЕЛЬНОСТЬ
        if ndvi > 0.10:
            return "разреженная растительность"
        # СУХОЙ ГРУНТ
        if ndwi_val < -0.06:
            return "сухой грунт / почва"
        # ПЕРЕХОДНЫЙ / СМЕШАННЫЙ
        return "смешанный / переходный"

    df["dominant_class"] = df.apply(_dominant, axis=1)

    # Цвет
    df["color"] = [CLUSTER_PALETTE[i % len(CLUSTER_PALETTE)] for i in range(len(df))]

    return df


def compute_cluster_spectral_stats(
    cube_reader,
    label_map_full: np.ndarray,
    n_clusters: int,
    band_indices: list[int],
    max_per_cluster: int = 2000,
    seed: int = RANDOM_SEED,
) -> dict[int, dict]:
    """Вычислить средний спектр ± std для каждого кластера (memmap, только нужные строки).

    Оптимизация: для каждого кластера читаем ТОЛЬКО строки, в которых есть
    пиксели этого кластера (в среднем ~10-20 уникальных строк на кластер из 2000 пикселей).
    """
    info = cube_reader.scene_info
    img_path = str(info.image_path)
    raw = np.memmap(img_path, dtype=np.float32, mode='r',
                    shape=(info.lines, info.bands, info.samples))
    n_bands = len(band_indices)
    stats = {}
    for c in range(n_clusters):
        rows_all, cols_all = np.where(label_map_full == c)
        n_pix = len(rows_all)
        if n_pix < 2:
            stats[c] = {"mean_spectrum": np.array([]), "std_spectrum": np.array([]), "n": 0}
            continue
        if n_pix > max_per_cluster:
            rng = np.random.default_rng(seed + c)
            idx = rng.choice(n_pix, max_per_cluster, replace=False)
            rows_all, cols_all = rows_all[idx], cols_all[idx]

        n_used = len(rows_all)
        spec = np.full((n_used, n_bands), np.nan, dtype=np.float32)

        # Группируем пиксели по строкам — читаем каждую строку один раз
        unique_rows = np.unique(rows_all)
        for r in unique_rows:
            row_mask = rows_all == r
            row_cols = cols_all[row_mask]
            # Читаем одну строку для всех полос
            row_data = np.array(raw[r, :, :], dtype=np.float32, copy=True)  # (425, 700)
            row_data = row_data[band_indices, :]  # (n_bands, 700)
            if info.nodata_value is not None:
                row_data[row_data == float(info.nodata_value)] = np.nan
            if info.scale_factor != 1.0:
                row_data = row_data * info.scale_factor
            # Извлекаем пиксели
            for j, col in enumerate(row_cols):
                spec[np.where(row_mask)[0][j]] = row_data[:, col]

        spec[~np.isfinite(spec)] = np.nan
        mean_spec = np.nanmean(spec, axis=0)
        std_spec = np.nanstd(spec, axis=0)
        stats[c] = {"mean_spectrum": mean_spec, "std_spectrum": std_spec, "n": n_pix}
    return stats


# ===================================================================
# 6. Визуализация
# ===================================================================

def save_cluster_visualization(
    rgb_image: np.ndarray,
    label_map: np.ndarray,
    output_path: Path,
    method_name: str,
    n_clusters: int,
    wavelengths_nm: np.ndarray,
    cluster_stats: dict[int, dict],
    band_indices: list[int],
    cluster_df: pd.DataFrame,
) -> None:
    """Сохранить эталонную раскладку: RGB слева, карта кластеров справа,
    колорбар, легенда с доминирующими классами, спектры кластеров."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Маскирование невалидных
    masked_labels = np.ma.masked_where(label_map < 0, label_map.astype(np.float32))

    colors = [CLUSTER_PALETTE[i % len(CLUSTER_PALETTE)] for i in range(n_clusters)]
    cmap = ListedColormap(colors[:n_clusters], name=f"{method_name}_clusters")

    fig = plt.figure(figsize=(18, 13), dpi=150, facecolor="white")

    # Три панели: RGB | Карта кластеров | Спектры
    gs = fig.add_gridspec(2, 3, height_ratios=[3, 1],
                          left=0.04, right=0.97, top=0.94, bottom=0.06,
                          hspace=0.35, wspace=0.08)

    # --- RGB ---
    ax_rgb = fig.add_subplot(gs[0, 0])
    ax_rgb.imshow(rgb_image)
    ax_rgb.set_title("RGB (R=660, G=550, B=460 нм)", fontsize=11, fontweight="bold")
    ax_rgb.axis("off")

    # --- Карта кластеров ---
    ax_cl = fig.add_subplot(gs[0, 1])
    im = ax_cl.imshow(masked_labels, cmap=cmap, interpolation="nearest", vmin=0, vmax=n_clusters - 1)
    ax_cl.set_title(f"{method_name}: K={n_clusters}", fontsize=11, fontweight="bold")
    ax_cl.axis("off")

    # --- Спектры кластеров ---
    ax_spec = fig.add_subplot(gs[0, 2])
    wl_used = wavelengths_nm[band_indices]
    for c in range(n_clusters):
        st = cluster_stats.get(c, {})
        mean_s = st.get("mean_spectrum", np.array([]))
        if len(mean_s) > 0:
            color = CLUSTER_PALETTE[c % len(CLUSTER_PALETTE)]
            ax_spec.plot(wl_used, mean_s, color=color, linewidth=1.2, label=f"C{c}")
    ax_spec.set_xlabel("Длина волны, нм", fontsize=9)
    ax_spec.set_ylabel("Reflectance", fontsize=9)
    ax_spec.set_title("Средние спектры кластеров", fontsize=10, fontweight="bold")
    ax_spec.legend(fontsize=7, ncol=2, loc="upper right")
    ax_spec.tick_params(labelsize=7)
    ax_spec.grid(True, alpha=0.3)

    # --- Легенда (таблица) ---
    ax_leg = fig.add_subplot(gs[1, :])
    ax_leg.axis("off")

    # Текстовая легенда с доминирующими классами
    legend_text = ""
    for _, row in cluster_df.iterrows():
        cid = int(row["cluster_id"])
        dom = row.get("dominant_class", "?")
        cnt = int(row["pixel_count"])
        frac = row["pixel_fraction"]
        color_hex = row.get("color", CLUSTER_PALETTE[cid % len(CLUSTER_PALETTE)])
        ndvi_v = row.get("mean_ndvi", np.nan)
        if np.isfinite(ndvi_v):
            legend_text += f"  C{cid}: {dom}  |  NDVI={ndvi_v:+.3f}  |  N={cnt:,} ({frac:.1%})\n"
        else:
            legend_text += f"  C{cid}: {dom}  |  N={cnt:,} ({frac:.1%})\n"

    ax_leg.text(0.02, 0.95, legend_text.strip(), transform=ax_leg.transAxes,
                fontsize=8.5, fontfamily="monospace", verticalalignment="top")

    # --- Колорбар ---
    cbar_ax = fig.add_axes([0.06, 0.025, 0.88, 0.015])
    cb = plt.colorbar(im, cax=cbar_ax, orientation="horizontal",
                      ticks=range(n_clusters))
    cb.set_label("Номер кластера", fontsize=9, fontweight="bold")
    cb.ax.tick_params(labelsize=8)

    fig.suptitle(
        f"Спектральная кластеризация  |  AVIRIS-NG reflectance  |  {method_name}",
        fontsize=14, fontweight="bold", y=0.985,
    )

    fig.savefig(output_path, bbox_inches="tight", dpi=150, facecolor="white", edgecolor="none")
    plt.close(fig)
    print(f"   ✓ Визуализация: {output_path.relative_to(ROOT)}")


def save_elbow_plot(scores: list[dict], output_path: Path) -> None:
    """Сохранить elbow-график."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ks = [s["k"] for s in scores]
    inertias = [s["inertia"] for s in scores]
    sils = [s["silhouette"] for s in scores]
    dbs = [s["davies_bouldin"] for s in scores]

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), dpi=150)
    axes[0].plot(ks, inertias, "bo-", markersize=6)
    axes[0].set_title("Inertia (Elbow)", fontweight="bold")
    axes[0].set_xlabel("K")
    axes[0].grid(True, alpha=0.3)
    axes[1].plot(ks, sils, "go-", markersize=6)
    axes[1].set_title("Silhouette Score ↑", fontweight="bold")
    axes[1].set_xlabel("K")
    axes[1].grid(True, alpha=0.3)
    axes[2].plot(ks, dbs, "ro-", markersize=6)
    axes[2].set_title("Davies-Bouldin Index ↓", fontweight="bold")
    axes[2].set_xlabel("K")
    axes[2].grid(True, alpha=0.3)
    fig.suptitle("Выбор числа кластеров K", fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"   ✓ Elbow-график: {output_path.relative_to(ROOT)}")


def save_cluster_legend(method_name: str, n_clusters: int, cluster_df: pd.DataFrame, output_path: Path) -> None:
    """Сохранить отдельную легенду кластеров."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 0.4 * n_clusters + 1.5), dpi=150)
    ax.axis("off")
    y = 0.95
    for _, row in cluster_df.iterrows():
        cid = int(row["cluster_id"])
        dom = row.get("dominant_class", "?")
        cnt = int(row["pixel_count"])
        color_hex = row.get("color", CLUSTER_PALETTE[cid % len(CLUSTER_PALETTE)])
        ndvi_v = row.get("mean_ndvi", np.nan)
        ndvi_str = f"NDVI={ndvi_v:+.3f}" if np.isfinite(ndvi_v) else ""
        ax.add_patch(mpatches.Rectangle((0.02, y - 0.035), 0.06, 0.04,
                                        facecolor=color_hex, edgecolor="#333", linewidth=1.5,
                                        transform=fig.transFigure))
        fig.text(0.10, y - 0.015, f"Кластер {cid}: {dom}  {ndvi_str}  ({cnt:,} пикселей)",
                 fontsize=9, va="center", fontfamily="monospace")
        y -= 0.06
    fig.suptitle(f"Легенда кластеров — {method_name}, K={n_clusters}", fontsize=12, fontweight="bold")
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"   ✓ Легенда: {output_path.relative_to(ROOT)}")


# ===================================================================
# 7. Сохранение GeoTIFF
# ===================================================================

def save_label_tiff(
    path: Path,
    label_map: np.ndarray,
    src_dataset,
    method_name: str,
    n_clusters: int,
) -> None:
    """Сохранить карту меток как GeoTIFF int16."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = label_map.astype(np.int16)
    write_tiff(
        path, data, src_dataset,
        metadata={
            "method": method_name,
            "n_clusters": str(n_clusters),
            "type": "cluster_labels",
        },
    )
    print(f"   ✓ GeoTIFF: {path.relative_to(ROOT)}")


# ===================================================================
# 8. Главный пайплайн
# ===================================================================

def run_clustering(
    method: str,
    n_clusters: int,
    data_reduced: np.ndarray,
    valid_mask: np.ndarray,
    pca,
    cube_reader,
    band_indices: list[int],
    args,
    full_reduced: np.ndarray | None = None,
) -> dict | None:
    """Запустить кластеризацию указанным методом.

    Returns:
        Словарь с результатами или None при ошибке.
    """
    result: dict = {"method": method, "n_clusters": n_clusters}
    info = cube_reader.scene_info

    # --- Обучение (с L2-нормализацией для косинусного расстояния) ---
    if method == "kmeans":
        model, train_labels, train_norm = fit_kmeans(data_reduced, n_clusters)
    else:
        model, train_labels, train_norm = fit_gmm(data_reduced, n_clusters)
    result["model"] = model

    # --- Оценка на нормализованных данных ---
    metrics = evaluate_clustering(train_norm, train_labels)
    result["metrics"] = metrics
    print(f"   Silhouette: {metrics['silhouette']:.4f}")
    print(f"   Davies-Bouldin: {metrics['davies_bouldin']:.4f}")

    # --- Предсказание полного изображения (с нормализацией) ---
    print(f"\n📊 Применение {method.upper()} к полному изображению...")
    from sklearn.preprocessing import normalize
    predict_data = full_reduced if full_reduced is not None else data_reduced
    predict_norm = normalize(predict_data.astype(np.float64), norm='l2')
    sub_rows, sub_cols = np.where(valid_mask)
    n_valid = len(sub_rows)

    if method == "kmeans":
        full_labels_flat = predict_full_kmeans(model, predict_norm)
    else:
        full_labels_flat, full_probs_flat = predict_full_gmm(model, predict_norm)
        result["probabilities"] = full_probs_flat

    # Построение полной карты меток
    label_map_full = np.full(valid_mask.shape, -1, dtype=np.int16)
    label_map_full[sub_rows, sub_cols] = full_labels_flat.astype(np.int16)

    # --- RGB через memmap (быстрее чем GDAL read_rgb) ---
    print("🎨 Построение RGB (memmap)...")
    idx_r, _ = find_nearest_band(info.wavelengths_nm, 660.0)
    idx_g, _ = find_nearest_band(info.wavelengths_nm, 550.0)
    idx_b, _ = find_nearest_band(info.wavelengths_nm, 460.0)
    raw_rgb = np.memmap(
        str(cube_reader.scene_info.image_path),
        dtype=np.float32, mode='r',
        shape=(info.lines, info.bands, info.samples),
    )
    r_band = np.array(raw_rgb[:, idx_r, :], dtype=np.float32, copy=True)
    g_band = np.array(raw_rgb[:, idx_g, :], dtype=np.float32, copy=True)
    b_band = np.array(raw_rgb[:, idx_b, :], dtype=np.float32, copy=True)
    if info.nodata_value is not None:
        for band in (r_band, g_band, b_band):
            band[band == float(info.nodata_value)] = np.nan
    rgb = np.stack([
        _percentile_stretch(r_band),
        _percentile_stretch(g_band),
        _percentile_stretch(b_band),
    ], axis=-1)
    rgb = (rgb * 255).astype(np.uint8)

    # --- Кросс-референс с индексами ---
    print("🔗 Кросс-референс с индексами (NDVI, NDWI, MNDWI, NDBI, PRI)...")
    cluster_df = compute_cross_indices(cube_reader, label_map_full, n_clusters)

    # --- Спектральные статистики ---
    print("📈 Средние спектры кластеров...")
    cluster_stats = compute_cluster_spectral_stats(
        cube_reader, label_map_full, n_clusters, band_indices,
    )

    # --- Внутрикластерная однородность ---
    intra_stds = []
    for c in range(n_clusters):
        st = cluster_stats.get(c, {})
        mean_s = st.get("mean_spectrum", np.array([]))
        std_s = st.get("std_spectrum", np.array([]))
        if len(std_s) > 0:
            intra_stds.append(float(np.mean(std_s)))
        else:
            intra_stds.append(float("nan"))
    cluster_df["intra_cluster_std"] = intra_stds

    # --- Силуэт на полной выборке (подвыборка) ---
    try:
        from sklearn.metrics import silhouette_score
        n_sil = min(10_000, n_valid)
        rng = np.random.default_rng(RANDOM_SEED)
        sil_idx = rng.choice(n_valid, n_sil, replace=False)
        sil_sample = silhouette_score(
            predict_norm[sil_idx], full_labels_flat[sil_idx],
        )
        cluster_df["silhouette_full"] = sil_sample
    except Exception:
        cluster_df["silhouette_full"] = float("nan")

    result["label_map"] = label_map_full
    result["cluster_df"] = cluster_df
    result["cluster_stats"] = cluster_stats
    result["rgb"] = rgb

    # --- Сохранение ---
    method_slug = method.lower()

    # GeoTIFF
    if not args.no_tiff:
        tiff_path = args.output_root / "clusters" / f"clusters_{method_slug}_k{n_clusters}.tif"
        save_label_tiff(tiff_path, label_map_full, cube_reader.dataset, method.upper(), n_clusters)
        if method == "gmm" and "probabilities" in result:
            # Карта неопределённости
            max_prob = result["probabilities"].max(axis=1)
            uncertainty = 1.0 - max_prob
            unc_map = np.full(valid_mask.shape, np.nan, dtype=np.float32)
            unc_map[sub_rows, sub_cols] = uncertainty.astype(np.float32)
            unc_path = args.output_root / "clusters" / f"uncertainty_{method_slug}_k{n_clusters}.tif"
            write_tiff(
                unc_path, unc_map, cube_reader.dataset,
                metadata={"type": "gmm_uncertainty", "method": "GMM",
                          "n_clusters": str(n_clusters)},
            )
            print(f"   ✓ Карта неопределённости: {unc_path.relative_to(ROOT)}")

    # CSV
    csv_path = args.output_root / "metadata" / f"cluster_summary_{method_slug}.csv"
    cluster_df.to_csv(csv_path, index=False)
    print(f"   ✓ Таблица кластеров: {csv_path.relative_to(ROOT)}")

    # Визуализации
    if not args.no_viz:
        viz_path = args.output_root / "cluster_previews" / f"clusters_{method_slug}_k{n_clusters}.png"
        save_cluster_visualization(
            rgb, label_map_full, viz_path, method.upper(), n_clusters,
            cube_reader.scene_info.wavelengths_nm, cluster_stats, band_indices, cluster_df,
        )
        leg_path = args.output_root / "legends" / f"legend_{method_slug}_k{n_clusters}.png"
        save_cluster_legend(method.upper(), n_clusters, cluster_df, leg_path)

    return result


# ===================================================================
# main()
# ===================================================================

def main() -> None:
    args = _parse_args()

    # --- 1. Проверка датасета ---
    if not args.input.exists():
        print(f"❌ ОШИБКА: датасет не найден: {args.input}")
        raise SystemExit(1)

    # --- 2. Открытие куба ---
    print(f"📂 Открытие ENVI куба: {args.input}")
    reader = open_envi_cube(args.input)
    info = reader.scene_info
    print(f"   Полос: {info.bands}, Строк: {info.lines}, Столбцов: {info.samples}")
    print(f"   Диапазон: {info.wavelengths_nm[0]:.1f} – {info.wavelengths_nm[-1]:.1f} нм")

    # --- 3. Выбор полос ---
    good_bands = _good_band_indices(info.wavelengths_nm, step=args.band_step)
    print(f"   Используется полос: {len(good_bands)} из {info.bands} "
          f"(исключены полосы водяного поглощения 1350-1450, 1800-1950 нм)")

    # --- 4. Валидная маска ---
    print("\n🔍 Построение маски валидных пикселей...")
    valid_mask = build_valid_mask(reader, good_bands)
    n_valid = int(np.sum(valid_mask))
    n_total = info.lines * info.samples
    print(f"   Валидных: {n_valid:,} из {n_total:,} ({n_valid / n_total:.2%})")

    # --- 5. Извлечение тренировочных данных ---
    print("\n📦 Подготовка тренировочных данных...")
    train_data = extract_training_data(reader, good_bands, valid_mask, args.max_train_pixels)

    # --- 6. PCA ---
    pca, train_reduced = apply_pca(train_data, args.pca_components)

    # --- 7. Elbow-анализ (опционально, пропускаем если K задано явно) ---
    print(f"\n📐 Elbow-анализ для выбора K (целевое K={args.n_clusters})...")
    # Пропускаем полный elbow — используем заданное K
    elbow_scores = [{"k": args.n_clusters, "inertia": 0, "silhouette": 0, "davies_bouldin": 0}]

    # --- 8. PCA-трансформация полного изображения ---
    print(f"\n🔄 PCA-трансформация полного изображения ({n_valid:,} пикселей, "
          f"spatial_step={args.spatial_step})...")
    full_reduced = transform_full_image_pca(
        reader, good_bands, valid_mask, pca, spatial_step=args.spatial_step,
    )

    # --- 9. Кластеризация ---
    results = {}
    methods = []
    if args.method in ("kmeans", "both"):
        methods.append("kmeans")
    if args.method in ("gmm", "both"):
        methods.append("gmm")

    for m in methods:
        print(f"\n{'='*60}")
        print(f"  КЛАСТЕРИЗАЦИЯ: {m.upper()}, K={args.n_clusters}")
        print(f"{'='*60}")
        res = run_clustering(
            m, args.n_clusters, train_reduced, valid_mask,
            pca, reader, good_bands, args, full_reduced,
        )
        if res:
            results[m] = res

    # --- 10. Итоговая сводка ---
    print(f"\n{'='*60}")
    print("  ИТОГОВАЯ СВОДКА")
    print(f"{'='*60}")
    for m, res in results.items():
        met = res["metrics"]
        print(f"  {m.upper()}: Silhouette={met['silhouette']:.4f}  "
              f"Davies-Bouldin={met['davies_bouldin']:.4f}")
        df = res["cluster_df"]
        print(f"    Кластеров: {len(df)}")
        for _, row in df.iterrows():
            print(f"      C{int(row['cluster_id'])}: {row['dominant_class']:<35s}  "
                  f"NDVI={row['mean_ndvi']:+.3f}  N={int(row['pixel_count']):,}")

    print(f"\n✅ Кластеризация завершена. Результаты в: {args.output_root.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main()
