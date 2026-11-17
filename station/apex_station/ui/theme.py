"""Dark industrial HMI theme: palette + Qt style sheet.

Design rules (keep the GUI consistent):
* one restrained accent colour (blue) for primary actions / selection only;
* status colours are reserved for status: green = OK/running, amber =
  warning/attention, red = fault/E-stop/critical;
* 8 px spacing grid, 8 px card radius, 5 px control radius;
* a modern sans-serif (Inter if installed, otherwise Segoe UI / Roboto / Noto).
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

BG = "#0e1217"
SIDEBAR = "#0a0d11"
SURFACE = "#151b23"
SURFACE_2 = "#1b232d"
SURFACE_3 = "#222c38"
BORDER = "#26303c"
TEXT = "#d9e0e8"
MUTED = "#8a97a6"
FAINT = "#5d6a79"
ACCENT = "#3d8bfd"
ACCENT_HOVER = "#5a9dff"
ACCENT_DIM = "#1f3b63"
OK = "#2fbf71"
WARN = "#f0a630"
BAD = "#e5484d"
INFO = "#4aa3df"

STATUS_COLORS = {
    # order statuses
    "Pending": WARN, "Approved": INFO, "Queued": INFO, "Loading": WARN, "InTransit": ACCENT, "In transit": ACCENT, "Delivered": OK,
    "Closed": FAINT, "Cancelled": BAD,
    # orchestrator steps (apex_orchestrator.job_machine.Step)
    "IDLE": OK, "WAIT_PLC_ACK": INFO, "UNDOCKING": ACCENT, "NAV_TO_TRANSFER": ACCENT, "FINAL_APPROACH": ACCENT,
    "WAIT_IN_POSITION": INFO, "LOADING": WARN, "LOAD_SHORTFALL": BAD, "NAV_TO_STATION": ACCENT,
    "AWAIT_RECEIPT": OK, "NAV_TO_RETURN": ACCENT, "AWAIT_RETURN_CONFIRM": WARN, "NAV_TO_DOCK": ACCENT,
    "DOCKING": ACCENT, "FAULT": BAD, "RESUME_WAIT": WARN, "OFFLINE": FAINT, "STARTING": FAINT,
    # cell modes
    "AUTO": OK, "MANUAL": WARN, "OFF": FAINT,
    # returns / discrepancies / parts
    "pending": WARN, "returned": OK,
    "Active": OK, "Inactive": FAINT, "Discontinued": BAD, "Damaged": WARN, "Missing": BAD, "NotLoaded": BAD,
    "ReturnMismatch": WARN,
}

FONT_CANDIDATES = ["Inter", "Segoe UI", "Roboto", "Noto Sans", "Cantarell", "DejaVu Sans"]


def pick_font() -> str:
    families = set(QFontDatabase.families())
    for name in FONT_CANDIDATES:
        if name in families:
            return name
    return QApplication.font().family()


def stylesheet(font: str) -> str:
    return f"""
* {{ font-family: "{font}"; }}
QWidget {{ background: {BG}; color: {TEXT}; font-size: 10pt; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}

/* ---------- sidebar ---------- */
#Sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
#Sidebar QLabel#Brand {{ color: {TEXT}; font-size: 15pt; font-weight: 700; letter-spacing: 1px; background: transparent; }}
#Sidebar QLabel#BrandSub {{ color: {MUTED}; font-size: 8pt; letter-spacing: 2px; background: transparent; }}
#Sidebar QPushButton {{
    background: transparent; color: {MUTED}; border: none; border-left: 3px solid transparent;
    text-align: left; padding: 11px 18px; font-size: 10.5pt; border-radius: 0px;
}}
#Sidebar QPushButton:hover {{ background: {SURFACE}; color: {TEXT}; }}
#Sidebar QPushButton:checked {{ background: {SURFACE}; color: {TEXT}; border-left: 3px solid {ACCENT}; font-weight: 600; }}

/* ---------- header / status bar ---------- */
#Header {{ background: {BG}; border-bottom: 1px solid {BORDER}; }}
#PageTitle {{ font-size: 16pt; font-weight: 600; background: transparent; }}
#PageSubtitle {{ color: {MUTED}; background: transparent; }}
#Clock {{ color: {MUTED}; font-size: 11pt; background: transparent; }}
QStatusBar {{ background: {SIDEBAR}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
QStatusBar QLabel {{ background: transparent; color: {MUTED}; padding: 0 8px; }}

/* ---------- cards ---------- */
QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#Card QLabel {{ background: transparent; }}
QLabel#CardTitle {{ color: {MUTED}; font-size: 8.5pt; font-weight: 600; letter-spacing: 1px; }}
QLabel#KpiValue {{ font-size: 24pt; font-weight: 600; }}
QLabel#KpiSub {{ color: {MUTED}; font-size: 9pt; }}
QLabel#Muted {{ color: {MUTED}; background: transparent; }}
QLabel#Big {{ font-size: 13pt; font-weight: 600; background: transparent; }}
QLabel#Section {{ color: {MUTED}; font-size: 8.5pt; font-weight: 600; letter-spacing: 1px; background: transparent; }}

/* ---------- inputs ---------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: #0f151c; border: 1px solid {BORDER}; border-radius: 5px; padding: 5px 8px;
    selection-background-color: {ACCENT_DIM};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {FAINT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_DIM}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}
QCheckBox {{ background: transparent; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER}; border-radius: 4px; background: #0f151c; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

/* ---------- buttons ---------- */
QPushButton {{
    background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 5px;
    padding: 6px 14px; min-height: 20px;
}}
QPushButton:hover {{ background: {SURFACE_3}; border-color: #344255; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE}; border-color: {SURFACE_2}; }}
QPushButton[variant="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[variant="primary"]:disabled {{ background: {ACCENT_DIM}; border-color: {ACCENT_DIM}; color: {MUTED}; }}
QPushButton[variant="danger"] {{ background: transparent; border-color: {BAD}; color: {BAD}; }}
QPushButton[variant="danger"]:hover {{ background: {BAD}; color: white; }}
QPushButton[variant="estop"] {{
    background: {BAD}; border: 2px solid #ff8a8d; color: white; font-weight: 800; font-size: 13pt;
    border-radius: 8px; padding: 14px; letter-spacing: 2px;
}}
QPushButton[variant="estop"]:hover {{ background: #ff5a5f; }}

/* ---------- tables ---------- */
QTableView, QTreeView, QListWidget {{
    background: {SURFACE}; alternate-background-color: #171e27; border: 1px solid {BORDER}; border-radius: 6px;
    gridline-color: transparent; selection-background-color: {ACCENT_DIM}; selection-color: {TEXT};
}}
QTableView::item, QListWidget::item {{ padding: 4px 6px; border: none; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {SURFACE_2}; color: {MUTED}; padding: 7px 8px; border: none; border-bottom: 1px solid {BORDER};
    font-size: 8.5pt; font-weight: 600;
}}
QTableCornerButton::section {{ background: {SURFACE_2}; border: none; }}

/* ---------- tabs ---------- */
QTabWidget::pane {{ border: none; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 8px 18px; border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}

/* ---------- misc ---------- */
QProgressBar {{ background: #0f151c; border: 1px solid {BORDER}; border-radius: 4px; text-align: center; height: 14px; color: {TEXT}; }}
QProgressBar::chunk {{ background: {OK}; border-radius: 3px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SURFACE_3}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: {BG}; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; }}
QMenu::item:selected {{ background: {ACCENT_DIM}; }}
QMessageBox, QDialog {{ background: {SURFACE}; }}
QDialog QLabel {{ background: transparent; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 6px; margin-top: 14px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; color: {MUTED}; padding: 0 4px; }}
QFrame#Toast {{ border-radius: 6px; border: 1px solid {BORDER}; background: {SURFACE_3}; }}
QFrame#Toast QLabel {{ background: transparent; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = pick_font()
    app.setFont(QFont(font, 10))
    pal = QPalette()
    for role, color in (
        (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, SURFACE),
        (QPalette.AlternateBase, SURFACE_2), (QPalette.Text, TEXT), (QPalette.Button, SURFACE_2),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT_DIM), (QPalette.HighlightedText, TEXT),
        (QPalette.ToolTipBase, SURFACE_3), (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, FAINT),
    ):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(font))
