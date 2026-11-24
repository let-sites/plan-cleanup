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
QPushButton:checked {{ background: #d6eaf4; border: 1px solid #4a90b8; color: #1f5f80; font-weight: 600; }}
QPushButton[kind="primary"] {{ background: {BLUE}; color: {BLUE_TEXT}; border: none; font-weight: 600; padding: 9px 18px; }}
QPushButton[kind="primary"]:hover {{ background: {BLUE_HOVER}; }}
QPushButton[kind="primary"]:disabled {{ background: #cfe4ee; color: #7f97a6; }}
QPushButton[kind="big"] {{ padding: 9px 18px; font-weight: 600; }}
QLineEdit, QDoubleSpinBox {{ background: white; border: 1px solid {LINE}; border-radius: 6px; padding: 6px 8px; min-height: 20px; }}
QComboBox {{ background: white; border: 1px solid #cfd6dc; border-radius: 8px; padding: 6px 30px 6px 12px; min-height: 20px; }}
QComboBox:hover {{ border-color: #aebbc6; background: #fbfcfd; }}
QComboBox:focus, QComboBox:on {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 26px; border: none; background: transparent; }}
QComboBox::down-arrow {{ image: url(@CHEVRON@); width: 10px; height: 14px; }}
QComboBox QAbstractItemView {{ background: white; border: 1px solid {LINE}; border-radius: 8px; padding: 4px; outline: none;
    selection-background-color: #e3f1f8; selection-color: {INK}; }}
QTableWidget QComboBox {{ border: none; border-radius: 0; background: transparent; padding: 4px 26px 4px 8px; }}
QTableWidget QComboBox:hover {{ background: #f4f8fb; }}
QTabWidget#pilltabs::tab-bar {{ left: 0px; }}
QTabWidget#pilltabs QTabBar {{ background: transparent; }}
QTabWidget#pilltabs::pane {{ border: none; border-top: 1px solid {LINE}; background: white; top: -1px; }}
QTabWidget#pilltabs QTabBar::tab {{ background: #eef2f5; color: {INK}; border: 1px solid {LINE}; border-bottom: none;
    border-top-left-radius: 10px; border-top-right-radius: 10px; padding: 9px 22px; margin-right: 6px; font-size: 15px; }}
QTabWidget#pilltabs QTabBar::tab:hover {{ background: #e6ecf0; }}
QTabWidget#pilltabs QTabBar::tab:selected {{ background: white; font-weight: 700; border-bottom: 3px solid #5b93b3; }}
QListWidget, QTableWidget {{ background: white; border: 1px solid {LINE}; border-radius: 8px; }}
QListWidget::item:selected {{ background: #e3f1f8; color: {INK}; }}
QListWidget#sheets {{ background: {BG}; border: none; outline: none; }}
QListWidget#sheets::item {{ border-radius: 6px; padding: 4px 10px; margin: 1px 0; }}
QListWidget#sheets::item:hover {{ background: #edf3f7; }}
QListWidget#sheets::item:selected {{ background: {BLUE}; color: {BLUE_TEXT}; font-weight: 600; }}
QProgressBar {{ background: #e6eef3; border: none; border-radius: 4px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QStatusBar {{ background: white; color: {MUTED}; border-top: 1px solid {LINE}; }}
QLabel[kind="muted"] {{ color: {MUTED}; }}
QProgressDialog {{ background: white; }}
"""

def apply(app):
    chev = asset('chevron.svg')
    app.setStyleSheet(QSS.replace('@CHEVRON@', chev.as_posix() if chev else ''))
    icon = asset('icon.png')
    if icon:
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon)))

def asset(name):
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    p = base / 'assets' / name
    return p if p.exists() else None
