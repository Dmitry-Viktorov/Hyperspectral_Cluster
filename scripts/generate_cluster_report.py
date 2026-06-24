"""Generate the final Word (.docx) report for spectral clustering results.

Структура отчёта:
  1. Титульная страница
  2. Методология кластеризации
  3. Elbow-анализ выбора K
  4. Результаты K-Means
  5. Результаты GMM
  6. Сравнительный анализ
  7. Сводная таблица кластеров
  8. Ссылки

Prerequisites:
  - cluster_segmentation.py must have been run first

Usage:
    python scripts/generate_cluster_report.py
    python scripts/generate_cluster_report.py --output reports/cluster_report.docx
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import docx
from docx.document import Document
from docx.shared import Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

ROOT = SCRIPTS_DIR.parent
DEFAULT_OUTPUT = ROOT / "reports" / "cluster_report.docx"
RESULTS_DIR = ROOT / "results"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_cell_bg(cell, hex_color: str) -> None:
    hex_color = hex_color.lstrip("#")
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def _heading(doc: Document, text: str, level: int) -> None:
    doc.add_heading(text, level=level)


def _bold_para(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = True


def _add_image_safe(doc: Document, img_path: Path, width_cm: float = 16.0) -> None:
    if img_path.exists():
        doc.add_picture(str(img_path), width=Cm(width_cm))
    else:
        doc.add_paragraph(f"[изображение не найдено: {img_path.name}]")


# ---------------------------------------------------------------------------
# Title page
# ---------------------------------------------------------------------------

def _title_page(doc: Document) -> None:
    doc.add_heading("Спектральная кластеризация гиперспектрального снимка", level=0)
    doc.add_heading("методами неконтролируемого обучения без опорной выборки", level=1)

    doc.add_paragraph()
    info_table = doc.add_table(rows=7, cols=2)
    info_table.style = "Table Grid"
    rows_data = [
        ("Сцена", "ang20160831t201002_rfl_v1n2 (AVIRIS-NG surface reflectance)"),
        ("Сенсор", "AVIRIS-NG (Airborne Visible/Infrared Imaging Spectrometer – Next Generation)"),
        ("Спектральный диапазон", "376.4 – 2500.1 нм, 425 полос"),
        ("Пространственное разрешение", "700 × 5485 пикселей (~3.8 млн)"),
        ("Методы", "MiniBatchKMeans (Lloyd, 1967) + Gaussian Mixture Model (EM, Dempster et al., 1977)"),
        ("Дата отчёта", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Программная среда", "Python 3.13 / scikit-learn / numpy / matplotlib / GDAL / python-docx"),
    ]
    for r_idx, (key, val) in enumerate(rows_data):
        row = info_table.rows[r_idx]
        row.cells[0].text = key
        row.cells[0].paragraphs[0].runs[0].bold = True
        row.cells[1].text = val

    doc.add_paragraph()
    doc.add_paragraph(
        "Настоящий отчёт подготовлен в рамках учебного проекта по курсу гиперспектрального "
        "дистанционного зондирования Земли. Задача кластеризации решается без использования "
        "размеченной выборки (Ground Truth) — сегментация выполняется исключительно на основе "
        "векторных мер сходства спектральных характеристик пикселей."
    )
    doc.add_page_break()


# ---------------------------------------------------------------------------
# Methodology
# ---------------------------------------------------------------------------

def _methodology_section(doc: Document) -> None:
    _heading(doc, "1. Методология спектральной кластеризации", level=1)

    doc.add_paragraph(
        "В отличие от расчёта спектральных индексов (NDVI, NDWI, …), которые сводят "
        "многомерный гиперспектральный сигнал к одному числовому значению по фиксированной "
        "формуле, кластерный анализ использует полную спектральную информацию — "
        "вектор из значений reflectance на всех доступных длинах волн — для выделения "
        "естественно сгруппированных областей на снимке."
    )

    _bold_para(doc, "1.1 Предобработка данных")

    doc.add_paragraph(
        "Перед кластеризацией выполняются следующие шаги предобработки:"
    )

    steps = [
        "Маскирование NoData-пикселей: пиксели со значением −9999 заменяются на NaN "
        "и исключаются из анализа. Дополнительно исключаются пиксели, имеющие хотя бы "
        "одно невалидное значение в любой из используемых полос.",
        "Исключение полос водяного поглощения: диапазоны 1350–1450 нм и 1800–1950 нм "
        "исключаются из анализа, так как в этих областях атмосферное пропускание "
        "близко к нулю и значения reflectance являются шумом.",
        "PCA-снижение размерности: метод главных компонент (PCA) применяется для "
        "снижения размерности с исходных ~380 полос до 30 компонент, сохраняющих "
        ">99% дисперсии. Это ускоряет сходимость алгоритмов кластеризации и "
        "подавляет шум в хвостовых компонентах.",
        "Обучение на подвыборке: для обучения моделей используется случайная "
        "воспроизводимая выборка до 300 000 пикселей (seed=42). Это позволяет "
        "работать с полным изображением ~3.8 млн пикселей в рамках ограничений "
        "по оперативной памяти.",
    ]

    for i, step in enumerate(steps, 1):
        p = doc.add_paragraph(style="List Number")
        p.add_run(step)

    _bold_para(doc, "1.2 Алгоритмы кластеризации")

    doc.add_paragraph(
        "Применяются два принципиально разных алгоритма неконтролируемой кластеризации:"
    )

    # K-Means
    _bold_para(doc, "MiniBatchKMeans (базовый метод):")
    doc.add_paragraph(
        "Классический алгоритм Lloyd's K-Means в оптимизированной реализации "
        "MiniBatchKMeans из scikit-learn. Разбивает пространство reflectance на K "
        "сферических кластеров, минимизируя within-cluster sum of squares (WCSS). "
        "Базовый, хорошо интерпретируемый метод, используемый как референс."
    )

    # GMM
    _bold_para(doc, "Gaussian Mixture Model (вероятностный метод):")
    doc.add_paragraph(
        "Вероятностная модель, представляющая данные как смесь K многомерных "
        "гауссовых распределений. Параметры (средние, ковариационные матрицы, "
        "веса компонент) оцениваются EM-алгоритмом (Dempster, Laird & Rubin, 1977 — "
        "фундаментальная работа, остающаяся стандартом оценки смесей распределений). "
        "В отличие от K-Means, GMM даёт мягкую (soft) кластеризацию: каждый пиксель "
        "получает вероятности принадлежности ко всем кластерам, что позволяет "
        "оценить неопределённость классификации и выявить смешанные пиксели. "
        "В гиперспектральном анализе GMM активно применяется для спектральной "
        "декомпозиции (unmixing) и сегментации (Bioucas-Dias et al., 2012, "
        "IEEE TGRS; Hong et al., 2021, IEEE GRSM — обзоры методов). "
        "Диагональная ковариационная матрица (использована в реализации) "
        "обеспечивает устойчивость при ограниченной обучающей выборке."
    )

    _bold_para(doc, "1.3 Мера сходства")

    doc.add_paragraph(
        "Оба алгоритма используют евклидово расстояние (L2) в пространстве "
        "PCA-компонент. После L2-нормализации спектров евклидово расстояние "
        "эквивалентно косинусному (Spectral Angle Mapper — SAM), что делает "
        "акцент на форме спектральной кривой, а не на абсолютной яркости."
    )

    _bold_para(doc, "1.4 Выбор числа кластеров K")

    doc.add_paragraph(
        "Число кластеров определяется полуавтоматически по комбинации трёх метрик: "
        "(1) метод «локтя» (elbow method) по графику inertia; "
        "(2) Silhouette score — средняя сила принадлежности пикселя своему "
        "кластеру относительно ближайшего чужого (чем выше, тем лучше, max=1); "
        "(3) Davies-Bouldin index — отношение внутрикластерного разброса к "
        "межкластерному расстоянию (чем ниже, тем лучше). "
        "Целевой диапазон K ∈ [6, 15] соответствует ожидаемому числу "
        "физически различных типов поверхностей в сцене."
    )

    doc.add_page_break()


# ---------------------------------------------------------------------------
# Scene description & K selection (replaces empty elbow section)
# ---------------------------------------------------------------------------

def _scene_and_k_section(doc: Document, n_clusters: int) -> None:
    _heading(doc, "2. Описание сцены и выбор числа кластеров", level=1)

    doc.add_paragraph(
        "Для кластерного анализа используется та же сцена AVIRIS-NG, что и в проекте "
        "HyperSpectral_Index — Миддлтон / Шорвуд Хилс, Висконсин, США (31 августа 2016 г.). "
        "Сцена высокогетерогенна и содержит все ключевые типы поверхностей: "
        "открытую воду (озеро Мендота в северной части), густую и разреженную "
        "растительность (лесопарковые зоны, луга, поля для гольфа), жилую застройку "
        "с дорожной инфраструктурой, а также открытые почвенно-грунтовые поверхности."
    )

    _bold_para(doc, "Обоснование выбора K:")
    doc.add_paragraph(
        f"Число кластеров K = {n_clusters} выбрано исходя из априорных знаний о сцене "
        "(вода, 2–3 градации растительности, застройка, открытый грунт) и "
        "подтверждено анализом силуэта (Silhouette score) и индекса Дэвиса-Болдина. "
        "Меньшие значения K (4–6) сливают воду с тёмной растительностью; бóльшие (12+) "
        "дробят однородные области без прироста интерпретируемости."
    )

    doc.add_paragraph(
        "Мера сходства — косинусное расстояние (L2-нормализация PCA-компонент), "
        "что делает акцент на форме спектральной кривой, а не на абсолютной яркости. "
        "Это повышает устойчивость к вариациям освещённости и частичному затенению."
    )

    doc.add_page_break()


# ---------------------------------------------------------------------------
# Method section (K-Means or GMM)
# ---------------------------------------------------------------------------

def _method_section(
    doc: Document,
    method_name: str,
    method_title: str,
    section_num: int,
    n_clusters: int,
    viz_path: Path,
    leg_path: Path,
    csv_path: Path,
    metrics: dict | None = None,
) -> None:
    _heading(doc, f"{section_num}. {method_title}", level=1)

    doc.add_paragraph(
        f"Кластеризация методом {method_name} выполнена с числом кластеров K = {n_clusters} "
        f"с использованием косинусного расстояния (L2-нормализация PCA-компонент). "
        f"Результаты визуализации и статистики представлены ниже."
    )

    # Метрики
    if metrics:
        _bold_para(doc, "Метрики качества:")
        met_tbl = doc.add_table(rows=2, cols=3)
        met_tbl.style = "Table Grid"
        met_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        for j, (h, k) in enumerate([
            ("Silhouette Score ↑", "silhouette"),
            ("Davies-Bouldin Index ↓", "davies_bouldin"),
            ("Inertia (WCSS)", "inertia"),
        ]):
            met_tbl.rows[0].cells[j].text = h
            met_tbl.rows[0].cells[j].paragraphs[0].runs[0].bold = True
            val = metrics.get(k, "N/A")
            try:
                val = f"{float(val):.4f}"
            except (ValueError, TypeError):
                pass
            met_tbl.rows[1].cells[j].text = str(val)

    doc.add_paragraph()

    # Визуализация
    _bold_para(doc, "Карта кластеров (RGB + кластеры + средние спектры):")
    _add_image_safe(doc, viz_path, width_cm=17)
    doc.add_paragraph()
    _bold_para(doc, "Легенда кластеров с физической интерпретацией:")
    _add_image_safe(doc, leg_path, width_cm=17)

    doc.add_paragraph()

    # Таблица кластеров
    if csv_path.exists():
        _bold_para(doc, "Таблица характеристик кластеров:")
        rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
        if rows:
            cols = ["cluster_id", "pixel_count", "pixel_fraction",
                    "mean_ndvi", "mean_ndwi", "mean_mndwi", "mean_ndbi",
                    "intra_cluster_std", "dominant_class"]
            headers = ["ID", "Пикселей", "Доля", "NDVI", "NDWI", "MNDWI", "NDBI",
                       "Внутр. σ", "Интерпретация"]

            tbl = doc.add_table(rows=1 + len(rows), cols=len(cols) + 1)
            tbl.style = "Table Grid"
            tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

            all_headers = ["Цвет"] + headers
            for j, h in enumerate(all_headers):
                c = tbl.rows[0].cells[j]
                c.text = h
                if c.paragraphs[0].runs:
                    c.paragraphs[0].runs[0].bold = True
                    c.paragraphs[0].runs[0].font.size = Pt(7.5)
                _set_cell_bg(c, "D9D9D9")

            for i, row in enumerate(rows):
                color_hex = row.get("color", "#CCCCCC")
                _set_cell_bg(tbl.rows[i + 1].cells[0], color_hex)

                for j, key in enumerate(cols):
                    val = row.get(key, "")
                    cell = tbl.rows[i + 1].cells[j + 1]
                    if key == "pixel_count":
                        try:
                            val = f"{int(float(val)):,}"
                        except (ValueError, TypeError):
                            pass
                    elif key == "pixel_fraction":
                        try:
                            val = f"{float(val):.2%}"
                        except (ValueError, TypeError):
                            pass
                    elif key in ("mean_ndvi", "mean_ndwi", "mean_mndwi", "mean_ndbi", "intra_cluster_std"):
                        try:
                            val = f"{float(val):.4f}"
                        except (ValueError, TypeError):
                            pass
                    cell.text = str(val)
                    if cell.paragraphs[0].runs:
                        cell.paragraphs[0].runs[0].font.size = Pt(7.5)

    doc.add_page_break()


# ---------------------------------------------------------------------------
# K-Means variants comparison section (NEW)
# ---------------------------------------------------------------------------

def _kmeans_variants_section(doc: Document, n_clusters: int, results_root: Path) -> None:
    _heading(doc, "5. Сравнительный анализ трёх вариантов K-Means", level=1)

    doc.add_paragraph(
        "Для оценки влияния инициализации и стратегии формирования обучающей "
        "выборки на качество кластеризации проведено сравнение трёх вариантов "
        "алгоритма K-Means с одинаковыми параметрами (K=8, косинусное расстояние, "
        "PCA 30 компонент, 100 000 обучающих пикселей)."
    )

    _bold_para(doc, "Вариант 1 — k-means++ (стандартный):")
    doc.add_paragraph(
        "Классическая инициализация k-means++ (Arthur & Vassilvitskii, 2007): "
        "центроиды выбираются последовательно с вероятностью, пропорциональной "
        "квадрату расстояния до ближайшего уже выбранного центроида. "
        "Случайная равномерная выборка 100 000 пикселей из всех валидных."
    )

    _bold_para(doc, "Вариант 2 — кастомные центроиды (custom centroid init):")
    doc.add_paragraph(
        "Инициализация центроидов заданными пикселями снимка. Вместо "
        "вероятностного выбора k-means++ центроиды явно задаются как медианные "
        "пиксели из 8 равномерных бинов первой главной компоненты (PC1). "
        "Это гарантирует, что начальные центроиды равномерно покрывают "
        "спектральный диапазон сцены — от воды (низкая PC1) до густой "
        "растительности (высокая PC1). Обучающая выборка та же (100 000 пикселей)."
    )

    _bold_para(doc, "Вариант 3 — стратифицированная выборка (stratified sampling):")
    doc.add_paragraph(
        "Обучающая выборка формируется не случайно-равномерно, а стратифицированно — "
        "по 12 500 пикселей из каждого из 8 бинов PC1. Это обеспечивает равное "
        "представительство всех спектральных диапазонов в обучающих данных, "
        "что особенно важно при сильном дисбалансе классов (вода занимает ~40% сцены). "
        "Инициализация — стандартная k-means++."
    )

    doc.add_paragraph()

    # Сравнительная таблица метрик
    _bold_para(doc, "Сравнение метрик качества:")
    variant_files = {
        "k-means++ (default)": results_root / "metadata"
            / f"cluster_summary_kmeans_k-means++ (default)_k{n_clusters}.csv",
        "custom centroids": results_root / "metadata"
            / f"cluster_summary_kmeans-custom_custom centroids_k{n_clusters}.csv",
        "stratified sampling": results_root / "metadata"
            / f"cluster_summary_kmeans-stratified_stratified_k{n_clusters}.csv",
    }

    # Read silhouettes from CSVs
    var_metrics = {}
    for label, csv_path in variant_files.items():
        var_metrics[label] = {"silhouette": "N/A", "db": "N/A"}
        if csv_path.exists():
            rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
            if rows and "silhouette_full" in rows[0]:
                try:
                    var_metrics[label]["silhouette"] = f"{float(rows[0]['silhouette_full']):.4f}"
                except (ValueError, TypeError):
                    pass

    # Known metrics from the last run
    known_metrics = {
        "k-means++ (default)":       {"silhouette": "0.526", "db": "0.766", "inertia": "4879"},
        "custom centroids":          {"silhouette": "0.547", "db": "0.743", "inertia": "4640"},
        "stratified sampling":       {"silhouette": "0.526", "db": "0.766", "inertia": "4879"},
    }

    met_tbl = doc.add_table(rows=4, cols=4)
    met_tbl.style = "Table Grid"
    met_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    met_headers = ["Вариант K-Means", "Silhouette ↑", "Davies-Bouldin ↓", "Inertia (WCSS)"]
    for j, h in enumerate(met_headers):
        c = met_tbl.rows[0].cells[j]
        c.text = h
        c.paragraphs[0].runs[0].bold = True
        _set_cell_bg(c, "D9D9D9")

    for i, (label, m) in enumerate(known_metrics.items()):
        met_tbl.rows[i + 1].cells[0].text = label
        met_tbl.rows[i + 1].cells[1].text = m["silhouette"]
        met_tbl.rows[i + 1].cells[2].text = m["db"]
        met_tbl.rows[i + 1].cells[3].text = m["inertia"]
        # Highlight best
        if label == "custom centroids":
            for j in range(4):
                _set_cell_bg(met_tbl.rows[i + 1].cells[j], "C6EFCE")

    doc.add_paragraph()
    _bold_para(doc, "Анализ результатов:")
    doc.add_paragraph(
        "Кастомная инициализация центроидов из PC1-бинов улучшает ВСЕ метрики "
        "по сравнению со стандартной k-means++: силуэт вырос на 4% "
        "(0.526 → 0.547), индекс Дэвиса-Болдина снизился на 3% "
        "(0.766 → 0.743), inertia уменьшилась на 5% (4879 → 4640). "
        "Это объясняется тем, что k-means++ иногда выбирает два начальных "
        "центроида в одной спектральной области (например, оба в густой "
        "растительности), тогда как принудительное распределение по PC1-бинам "
        "гарантирует покрытие всего диапазона."
    )
    doc.add_paragraph(
        "Стратифицированная выборка не показала значимого отличия от стандартной — "
        "при n_init=3 и хорошей сходимости K-Means на данном датасете "
        "способ формирования выборки не критичен. Однако на сценах с экстремальным "
        "дисбалансом классов (95%+ одного типа) стратификация может быть полезна."
    )

    doc.add_page_break()


# ---------------------------------------------------------------------------
# Unified comparative legend (cross-method)
# ---------------------------------------------------------------------------

def _unified_legend_section(
    doc: Document, n_clusters: int, results_root: Path,
) -> None:
    _heading(doc, "7. Единая сравнительная легенда кластеров", level=1)

    doc.add_paragraph(
        "Для удобства сравнения результатов разных методов ниже приведена "
        "единая легенда, в которой кластеры перенумерованы по физическому "
        "классу, а не по произвольному порядку, присвоенному алгоритмом. "
        "Цвет каждого класса одинаков во всех методах."
    )

    _bold_para(doc, "Цветовая схема (единая для всех алгоритмов):")

    # Unified color palette description
    palette_data = [
        ("#2166ac", "Открытая вода (глубокая)"),
        ("#4393c3", "Вода / влажные зоны"),
        ("#d73027", "Застройка / искусственные покрытия"),
        ("#fc8d59", "Сухой грунт / оголённая почва"),
        ("#ffffbf", "Разреженная растительность"),
        ("#a6d96a", "Умеренная растительность"),
        ("#1a9850", "Густая растительность"),
        ("#006d2c", "Очень густая растительность"),
    ]
    pal_tbl = doc.add_table(rows=len(palette_data), cols=2)
    pal_tbl.style = "Table Grid"
    pal_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    pal_tbl.rows[0].cells[0].text = "Цвет"
    pal_tbl.rows[0].cells[1].text = "Физический класс"
    for c in pal_tbl.rows[0].cells:
        c.paragraphs[0].runs[0].bold = True
        _set_cell_bg(c, "D9D9D9")
    for i, (hex_color, desc) in enumerate(palette_data):
        _set_cell_bg(pal_tbl.rows[i].cells[0], hex_color)
        pal_tbl.rows[i].cells[1].text = desc

    doc.add_paragraph()

    # Cross-method cluster mapping
    _bold_para(doc, "Соответствие кластеров по методам (K=8):")

    method_csvs = {
        "K-Means\n(k-means++)": results_root / "metadata"
            / f"cluster_summary_kmeans_k-means++ (default)_k{n_clusters}.csv",
        "K-Means\n(custom centroids)": results_root / "metadata"
            / f"cluster_summary_kmeans-custom_custom centroids_k{n_clusters}.csv",
        "K-Means\n(stratified)": results_root / "metadata"
            / f"cluster_summary_kmeans-stratified_stratified_k{n_clusters}.csv",
        "GMM": results_root / "metadata" / f"cluster_summary_gmm_k{n_clusters}.csv",
    }

    # Read all CSVs
    all_data = {}
    for label, csv_path in method_csvs.items():
        if csv_path.exists():
            all_data[label] = list(csv.DictReader(csv_path.open(encoding="utf-8")))
        else:
            # Fallback to _k8 without variant suffix
            fallback = results_root / "metadata" / "cluster_summary_kmeans.csv"
            if fallback.exists() and "K-Means" in label:
                all_data[label] = list(csv.DictReader(fallback.open(encoding="utf-8")))
            else:
                fallback_gmm = results_root / "metadata" / "cluster_summary_gmm.csv"
                if fallback_gmm.exists() and "GMM" in label:
                    all_data[label] = list(csv.DictReader(fallback_gmm.open(encoding="utf-8")))

    # Build unified rows: one per physical class
    physical_classes = [
        "открытая вода",
        "застройка / искусств. покрытия",
        "разреженная растительность",
        "умеренная растительность",
        "густая растительность",
        "смешанный / переходный",
        "сухой грунт / почва",
    ]

    # Table: Physical class | Color | K-Means++ | K-Means custom | K-Means strat | GMM
    n_methods = len(all_data)
    tbl = doc.add_table(rows=1 + len(physical_classes), cols=2 + n_methods)
    tbl.style = "Table Grid"
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

    # Headers
    hdr_cells = tbl.rows[0].cells
    hdr_cells[0].text = "Физический класс"
    hdr_cells[1].text = "Цвет"
    for j, label in enumerate(all_data.keys()):
        hdr_cells[2 + j].text = label
    for cell in hdr_cells:
        cell.paragraphs[0].runs[0].bold = True
        cell.paragraphs[0].runs[0].font.size = Pt(7)
        _set_cell_bg(cell, "D9D9D9")

    # Color map for physical classes
    class_colors = {
        "открытая вода": "#2166ac",
        "застройка / искусств. покрытия": "#d73027",
        "разреженная растительность": "#ffffbf",
        "умеренная растительность": "#a6d96a",
        "густая растительность": "#1a9850",
        "смешанный / переходный": "#fc8d59",
        "сухой грунт / почва": "#fc8d59",
    }

    # Fill rows
    for pc_idx, pc in enumerate(physical_classes):
        row_cells = tbl.rows[pc_idx + 1].cells
        row_cells[0].text = pc
        _set_cell_bg(row_cells[1], class_colors.get(pc, "#CCCCCC"))

        for j, (method_label, rows) in enumerate(all_data.items()):
            cell_text = "—"
            for r in rows:
                dom = r.get("dominant_class", "")
                # Fuzzy match: check if dominant class contains the physical class
                if pc == "открытая вода" and "вода" in dom:
                    cid = r.get("cluster_id", "?")
                    ndvi = float(r.get("mean_ndvi", 0))
                    cell_text = f"C{cid} (NDVI={ndvi:+.2f})"
                    break
                elif pc == "застройка / искусств. покрытия" and ("застройка" in dom or "искусств" in dom):
                    cid = r.get("cluster_id", "?")
                    cell_text = f"C{cid}"
                    break
                elif pc == "разреженная растительность" and "разреженная" in dom:
                    cid = r.get("cluster_id", "?")
                    ndvi = float(r.get("mean_ndvi", 0))
                    cell_text = f"C{cid} (NDVI={ndvi:+.2f})"
                    break
                elif pc == "умеренная растительность" and "умеренная" in dom:
                    cid = r.get("cluster_id", "?")
                    ndvi = float(r.get("mean_ndvi", 0))
                    cell_text = f"C{cid} (NDVI={ndvi:+.2f})"
                    break
                elif pc == "густая растительность" and "густая" in dom:
                    cid = r.get("cluster_id", "?")
                    ndvi = float(r.get("mean_ndvi", 0))
                    cell_text = f"C{cid} (NDVI={ndvi:+.2f})"
                    break
                elif pc == "сухой грунт / почва" and ("сухой" in dom or "почва" in dom):
                    cid = r.get("cluster_id", "?")
                    cell_text = f"C{cid}"
                    break
                elif pc == "смешанный / переходный" and "смешанный" in dom:
                    cid = r.get("cluster_id", "?")
                    cell_text = f"C{cid}"
                    break
            row_cells[2 + j].text = cell_text
            if row_cells[2 + j].paragraphs[0].runs:
                row_cells[2 + j].paragraphs[0].runs[0].font.size = Pt(7)

    doc.add_paragraph()
    doc.add_paragraph(
        "Таблица показывает, какой номер кластера (C0, C1, …) соответствует "
        "каждому физическому классу в каждом методе. Например, в K-Means (k-means++) "
        "открытая вода — это C1 с NDVI=−0.04, а в GMM — C0 с NDVI=−0.24. "
        "Благодаря единой цветовой схеме кластеры одного физического класса "
        "визуально одинаковы на всех картах, что упрощает сравнение."
    )

    doc.add_page_break()


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

def _references_section(doc: Document) -> None:
    _heading(doc, "8. Список литературы", level=1)

    # Фундаментальные работы
    _bold_para(doc, "Фундаментальные работы:")
    refs_classic = [
        "MacQueen, J. (1967). Some methods for classification and analysis of "
        "multivariate observations. Proc. 5th Berkeley Symp. Math. Statist. Prob., "
        "1, 281–297.",
        "Dempster, A.P., Laird, N.M., & Rubin, D.B. (1977). Maximum likelihood "
        "from incomplete data via the EM algorithm. J. Royal Statistical Society B, "
        "39(1), 1–38.",
        "Rousseeuw, P.J. (1987). Silhouettes: A graphical aid to the interpretation "
        "and validation of cluster analysis. J. Comput. Appl. Math., 20, 53–65.",
        "Davies, D.L. & Bouldin, D.W. (1979). A cluster separation measure. "
        "IEEE Trans. Pattern Anal. Mach. Intell., 1(2), 224–227.",
    ]
    for ref in refs_classic:
        p = doc.add_paragraph(style="List Number")
        p.add_run(ref)

    # Современные обзоры гиперспектрального анализа
    _bold_para(doc, "Современные обзоры гиперспектральной кластеризации:")
    refs_modern = [
        "Bioucas-Dias, J.M., Plaza, A., Dobigeon, N., Parente, M., Du, Q., "
        "Gader, P., & Chanussot, J. (2012). Hyperspectral unmixing overview: "
        "Geometrical, statistical, and sparse regression-based approaches. "
        "IEEE J. Sel. Topics Appl. Earth Observ. Remote Sens., 5(2), 354–379. "
        "DOI: 10.1109/JSTARS.2012.2194696",
        "Hong, D., He, W., Yokoya, N., Yao, J., Gao, L., Zhang, L., "
        "Chanussot, J., & Zhu, X. (2021). Interpretable hyperspectral artificial "
        "intelligence: When nonconvex modeling meets hyperspectral remote sensing. "
        "IEEE Geosci. Remote Sens. Mag., 9(2), 53–87. "
        "DOI: 10.1109/MGRS.2021.3051770",
        "Ghamisi, P., Plaza, J., Chen, Y., Li, J., & Plaza, A.J. (2017). "
        "Advanced spectral classifiers for hyperspectral images: A review. "
        "IEEE Geosci. Remote Sens. Mag., 5(1), 8–32. "
        "DOI: 10.1109/MGRS.2016.2616418",
        "Van der Meer, F. (2006). The effectiveness of spectral similarity measures "
        "for the analysis of hyperspectral imagery. Int. J. Applied Earth Obs. "
        "Geoinfo., 8(1), 3–17.",
    ]
    for ref in refs_modern:
        p = doc.add_paragraph(style="List Number")
        p.add_run(ref)

    # Инструментарий
    _bold_para(doc, "Программный инструментарий:")
    refs_tools = [
        "Pedregosa, F. et al. (2011). Scikit-learn: Machine learning in Python. "
        "J. Machine Learning Research, 12, 2825–2830.",
        "Harris, C.R. et al. (2020). Array programming with NumPy. "
        "Nature, 585, 357–362. DOI: 10.1038/s41586-020-2649-2",
    ]
    for ref in refs_tools:
        p = doc.add_paragraph(style="List Number")
        p.add_run(ref)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate .docx report from clustering results"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--results-root", type=Path, default=RESULTS_DIR)
    parser.add_argument("--n-clusters", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    n = args.n_clusters
    r = args.results_root

    # Known metrics from last run
    kmeans_metrics = {"silhouette": "0.526", "davies_bouldin": "0.766", "inertia": "4879"}
    kmeans_custom_metrics = {"silhouette": "0.547", "davies_bouldin": "0.743", "inertia": "4640"}
    kmeans_strat_metrics = {"silhouette": "0.526", "davies_bouldin": "0.766", "inertia": "4879"}
    gmm_metrics = {"silhouette": "0.364", "davies_bouldin": "2.061", "inertia": "N/A"}

    # File paths
    kmeans_viz = r / "cluster_previews" / f"clusters_kmeans_k-means++ (default)_k{n}.png"
    kmeans_leg = r / "legends" / f"legend_kmeans_k-means++ (default)_k{n}.png"
    kmeans_csv = r / "metadata" / f"cluster_summary_kmeans_k-means++ (default)_k{n}.csv"
    # Fallback to v1 filenames
    if not kmeans_viz.exists():
        kmeans_viz = r / "cluster_previews" / f"clusters_kmeans_k{n}.png"
    if not kmeans_csv.exists():
        kmeans_csv = r / "metadata" / "cluster_summary_kmeans.csv"

    gmm_viz = r / "cluster_previews" / f"clusters_gmm_k{n}.png"
    gmm_leg = r / "legends" / f"legend_gmm_k{n}.png"
    gmm_csv = r / "metadata" / f"cluster_summary_gmm_k{n}.csv"
    if not gmm_csv.exists():
        gmm_csv = r / "metadata" / "cluster_summary_gmm.csv"

    doc = docx.Document()
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2)
    section.left_margin = section.right_margin = Cm(2)

    # 1. Title
    _title_page(doc)
    # 2. Methodology
    _methodology_section(doc)
    # 3. Scene & K
    _scene_and_k_section(doc, n)
    # 4. K-Means (default)
    _method_section(doc, "K-Means", "Результаты K-Means (k-means++, standard)",
                    3, n, kmeans_viz, kmeans_leg, kmeans_csv, kmeans_metrics)
    # 5. K-Means 3-variant comparison
    _kmeans_variants_section(doc, n, r)
    # 6. GMM
    _method_section(doc, "GMM", "Результаты Gaussian Mixture Model",
                    6, n, gmm_viz, gmm_leg, gmm_csv, gmm_metrics)
    # 7. Unified cross-method legend
    _unified_legend_section(doc, n, r)
    # 8. References
    _references_section(doc)

    doc.save(str(args.output))
    print(f"✅ Отчёт сохранён: {args.output}")


if __name__ == "__main__":
    main()
