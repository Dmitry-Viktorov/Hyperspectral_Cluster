# HyperSpectral_Cluster

**Неконтролируемая спектральная сегментация гиперспектрального снимка AVIRIS-NG методами кластерного анализа (без Ground Truth).**

Проект реализует полный пайплайн: предобработка → PCA → кластеризация (K-Means в 3 вариантах + GMM) → оценка качества → визуализация → DOCX-отчёт.

## Ключевые особенности

✅ **K-Means в 3 вариантах:** k-means++ (default), custom centroids (PC1 bins), stratified sampling  
✅ **GMM с мягкой кластеризацией:** вероятности + карта неопределённости  
✅ **Единая цветовая легенда** для всех методов — физическая привязка к классу  
✅ **Косинусное расстояние** (L2-нормализация PCA) — акцент на форме спектра  
✅ **Без Ground Truth** — полностью неконтролируемая сегментация  
✅ **PCA**: 125 полос → 30 компонент (>99% дисперсии)  
✅ **Кросс-референс**: авто-интерпретация кластеров через NDVI/NDWI/MNDWI/NDBI/PRI  
✅ **BIL-оптимизация**: numpy memmap вместо GDAL для 6.5 GB ENVI файлов  
✅ **GeoTIFF + PNG + CSV + DOCX** — полный набор выходных продуктов  

## Сравнение K-Means (K=8, cosine distance)

| Вариант | Inertia ↓ | Silhouette ↑ | DB ↓ |
|---------|-----------|-------------|------|
| k-means++ (default) | 4879 | 0.526 | 0.766 |
| **Custom centroids** | **4640** | **0.547** | **0.743** |
| Stratified sampling | 4879 | 0.526 | 0.766 |
| *GMM (референс)* | — | 0.364 | 2.061 |

## Быстрый старт

```bash
pip install -r requirements.txt

# Обычный запуск (K-Means + GMM):
python scripts/cluster_segmentation.py --method both --n-clusters 8

# Сравнение 3 вариантов K-Means:
python scripts/cluster_segmentation.py --method both --n-clusters 8 --kmeans-variants

# Генерация DOCX-отчёта:
python scripts/generate_cluster_report.py --n-clusters 8
```
```

### Шаг 2: Запуск кластеризации

```bash
# Полный пайплайн (K-Means + GMM, K=10):
python scripts/cluster_segmentation.py

# Только K-Means:
python scripts/cluster_segmentation.py --method kmeans --n-clusters 8

# Только GMM:
python scripts/cluster_segmentation.py --method gmm --n-clusters 12

# Быстрый тест (меньше пикселей для обучения):
python scripts/cluster_segmentation.py --n-clusters 7 --max-train-pixels 100000
```

**Примерное время выполнения:** 10–20 минут (зависит от K и числа пикселей).

### Шаг 3: Генерация DOCX отчёта

```bash
python scripts/generate_cluster_report.py
python scripts/generate_cluster_report.py --n-clusters 10
```

---

## Алгоритмы:

### K-Means (MiniBatch)
Классический алгоритм Lloyd's K-Means. Разбивает пространство reflectance на K сферических кластеров. Используется как базовый референсный метод.

### Gaussian Mixture Model (GMM)
Вероятностная модель смеси гауссовых распределений. EM-алгоритм (Dempster et al., 1977). Преимущества: мягкая кластеризация, эллипсоидальные кластеры, оценка неопределённости.

---

## Выходные продукты:

1. **Карты кластеров** — GeoTIFF (int16): `results/clusters/clusters_{method}_k{K}.tif`
2. **Карта неопределённости GMM** — GeoTIFF (float32): `results/clusters/uncertainty_gmm_k{K}.tif`
3. **Визуализации** — PNG (RGB + кластеры + спектры): `results/cluster_previews/`
4. **Легенды** — PNG: `results/legends/`
5. **Таблицы** — CSV: `results/metadata/cluster_summary_{method}.csv`
6. **Elbow-график** — PNG + CSV: `results/cluster_previews/_elbow_analysis.png`
7. **DOCX отчёт** — `reports/cluster_report.docx`

---

## Таблица кластеров (cluster_summary):

Столбцы аналогичны `index_summary.csv` из `HyperSpectral_Index`, но для каждого кластера:

| Столбец | Описание |
|---------|----------|
| `cluster_id` | Номер кластера (0..K−1) |
| `pixel_count` | Количество пикселей |
| `pixel_fraction` | Доля от всех классифицированных пикселей |
| `mean_ndvi` | Средний NDVI в кластере |
| `mean_ndwi` | Средний NDWI в кластере |
| `mean_mndwi` | Средний MNDWI в кластере |
| `mean_ndbi` | Средний NDBI в кластере |
| `intra_cluster_std` | Среднее СКО reflectance внутри кластера |
| `dominant_class` | Автоматическая интерпретация по индексам |
| `color` | HEX-цвет на визуализации |
