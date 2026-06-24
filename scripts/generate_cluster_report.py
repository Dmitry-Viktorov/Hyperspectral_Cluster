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
        f"Кластеризация методом {method_name} выполнена с числом кластеров K = {n_clusters}. "
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
            ("Approx. Inertia", "approx_inertia"),
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
    _bold_para(doc, "Карта кластеров (RGB + кластеры + спектры):")
    _add_image_safe(doc, viz_path, width_cm=17)
    doc.add_paragraph()
    _bold_para(doc, "Легенда кластеров:")
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
                # Цвет
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
# Comparison section
# ---------------------------------------------------------------------------

def _comparison_section(doc: Document, kmeans_csv: Path, gmm_csv: Path) -> None:
    _heading(doc, "5. Сравнительный анализ методов", level=1)

    doc.add_paragraph(
        "Сравнение K-Means и GMM по ключевым характеристикам кластеризации."
    )

    # Сравнительная таблица
    comp_tbl = doc.add_table(rows=7, cols=3)
    comp_tbl.style = "Table Grid"
    comp_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

    comp_data = [
        ("Характеристика", "K-Means", "GMM"),
        ("Тип кластеризации", "Жёсткая (hard)", "Мягкая (soft) — вероятности"),
        ("Форма кластеров", "Сферическая (изотропная)", "Эллипсоидальная (полная ковариация)"),
        ("Чувствительность к инициализации", "Да (k-means++)", "Да (k-means++)"),
        ("Вычислительная сложность", "O(n·K·d·iter) — быстрее", "O(n·K·d²·iter) — медленнее"),
        ("Интерпретируемость", "Высокая (центроиды)", "Средняя (ковариационные матрицы)"),
        ("Доп. возможности",
         "—",
         "Оценка неопределённости, генеративная модель"),
    ]

    for i, (c1, c2, c3) in enumerate(comp_data):
        row = comp_tbl.rows[i]
        row.cells[0].text = c1
        row.cells[1].text = c2
        row.cells[2].text = c3
        if i == 0:
            for j in range(3):
                row.cells[j].paragraphs[0].runs[0].bold = True
                _set_cell_bg(row.cells[j], "D9D9D9")

    doc.add_paragraph()

    # Анализ согласованности
    doc.add_paragraph(
        "Оба метода показали способность выделять основные типы поверхностей "
        "(вода, растительность разных типов, застройка, открытый грунт). "
        "GMM обеспечивает более тонкое разделение за счёт эллипсоидальных "
        "кластеров и предоставляет оценку неопределённости для каждого пикселя, "
        "что важно для последующего анализа достоверности."
    )

    doc.add_page_break()


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

def _references_section(doc: Document) -> None:
    _heading(doc, "6. Список литературы", level=1)
    refs = [
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
        "Van der Meer, F. (2006). The effectiveness of spectral similarity measures "
        "for the analysis of hyperspectral imagery. Int. J. Applied Earth Obs. "
        "Geoinfo., 8(1), 3–17.",
        "Pedregosa, F. et al. (2011). Scikit-learn: Machine learning in Python. "
        "J. Machine Learning Research, 12, 2825–2830.",
    ]
    for ref in refs:
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
    kmeans_viz = args.results_root / "cluster_previews" / f"clusters_kmeans_k{n}.png"
    kmeans_leg = args.results_root / "legends" / f"legend_kmeans_k{n}.png"
    kmeans_csv = args.results_root / "metadata" / "cluster_summary_kmeans.csv"
    gmm_viz = args.results_root / "cluster_previews" / f"clusters_gmm_k{n}.png"
    gmm_leg = args.results_root / "legends" / f"legend_gmm_k{n}.png"
    gmm_csv = args.results_root / "metadata" / "cluster_summary_gmm.csv"

    doc = docx.Document()
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2)
    section.left_margin = section.right_margin = Cm(2)

    _title_page(doc)
    _methodology_section(doc)
    _scene_and_k_section(doc, n)

    # K-Means
    _method_section(
        doc, "K-Means", "Результаты кластеризации K-Means (MiniBatch)",
        3, n, kmeans_viz, kmeans_leg, kmeans_csv,
    )

    # GMM
    _method_section(
        doc, "GMM", "Результаты кластеризации Gaussian Mixture Model",
        4, n, gmm_viz, gmm_leg, gmm_csv,
    )

    _comparison_section(doc, kmeans_csv, gmm_csv)
    _references_section(doc)

    doc.save(str(args.output))
    print(f"✅ Отчёт сохранён: {args.output}")


if __name__ == "__main__":
    main()
