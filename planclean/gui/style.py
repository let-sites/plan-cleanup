"""Look and feel, matched to LET's nell site: soft blue buttons, white cards, dark slate text."""
from pathlib import Path
import sys

BLUE = '#8ec5de'        # nell's main button
BLUE_HOVER = '#7ab8d4'
BLUE_TEXT = '#14344b'
ACCENT = '#4a90b8'      # the blue in the nell logo and links
INK = '#1f2a33'
MUTED = '#6b7a86'
LINE = '#dfe3e7'
BG = '#f6f7f8'
REVIEW = '#e8590c'      # items that need a look

QSS = f"""
QWidget {{ color: {INK}; font-size: 14px; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolBar {{ background: white; border: none; border-bottom: 1px solid {LINE}; padding: 8px 12px; spacing: 10px; }}
QDockWidget > QWidget, QTabWidget::pane {{ background: white; border: none; }}
QTabWidget > QWidget {{ background: white; }}
QPushButton {{ background: #f1f3f5; border: 1px solid {LINE}; border-radius: 6px; padding: 7px 14px; }}
QPushButton:hover {{ background: #e8ebee; }}
QPushButton[kind="primary"] {{ background: {BLUE}; color: {BLUE_TEXT}; border: none; font-weight: 600; padding: 9px 18px; }}
QPushButton[kind="primary"]:hover {{ background: {BLUE_HOVER}; }}
QPushButton[kind="primary"]:disabled {{ background: #cfe4ee; color: #7f97a6; }}
QPushButton[kind="big"] {{ padding: 9px 18px; font-weight: 600; }}
QComboBox, QLineEdit, QDoubleSpinBox {{ background: white; border: 1px solid {LINE}; border-radius: 6px; padding: 6px 8px; min-height: 20px; }}
QListWidget, QTableWidget {{ background: white; border: 1px solid {LINE}; border-radius: 8px; }}
QListWidget::item:selected {{ background: #e3f1f8; color: {INK}; }}
QListWidget#sheets {{ background: {BG}; border: none; }}
QListWidget#sheets::item {{ background: white; border: 1px solid {LINE}; border-radius: 8px; padding: 6px; margin: 2px; }}
QListWidget#sheets::item:selected {{ background: #e3f1f8; border: 2px solid {ACCENT}; }}
QStatusBar {{ background: white; color: {MUTED}; border-top: 1px solid {LINE}; }}
QLabel[kind="muted"] {{ color: {MUTED}; }}
QProgressDialog {{ background: white; }}
"""

def apply(app):
    app.setStyleSheet(QSS)
    icon = asset('icon.png')
    if icon:
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon)))

def asset(name):
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    p = base / 'assets' / name
    return p if p.exists() else None
