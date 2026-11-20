"""Zoomable sheet view with three modes: pan, draw a box, click a symbol."""
from __future__ import annotations
from PySide6.QtCore import Qt, QRectF, Signal, QPointF
from PySide6.QtGui import QPen, QColor, QBrush, QPainterPath, QPainter, QFont
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsRectItem

CLASS_COLORS = {
    'outlet': '#d62728', 'switch': '#1f77b4', 'dimmer': '#1f77b4', 'ceiling_light': '#9467bd', 'wall_light': '#ff7f0e',
    'recessed_can': '#2ca02c', 'disk_light': '#8c564b', 'fan': '#17becf', 'exhaust_fan': '#7f7f7f', 'smoke': '#bcbd22',
    'co_smoke': '#bcbd22', 'ceiling_outlet': '#e377c2', 'floor_outlet': '#e377c2', 'flood_light': '#ff7f0e', 'jbox': '#7f7f7f',
}

class SheetView(QGraphicsView):
    regionDrawn = Signal(QRectF)
    clicked = Signal(QPointF)
    itemPicked = Signal(object)

    def __init__(self):
        super().__init__()
        self.scene_ = QGraphicsScene(self); self.setScene(self.scene_)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.mode = 'pan'; self._start = None; self._band = None
        self.page_item = None; self.region_item = None; self.overlay = []
        self.setBackgroundBrush(QColor('#e9e9e9'))

    def set_sheet(self, pixmap):
        self.scene_.clear(); self.overlay = []; self.region_item = None
        self.page_item = self.scene_.addPixmap(pixmap)
        self.scene_.setSceneRect(QRectF(pixmap.rect()))
        self.fitInView(self.page_item, Qt.KeepAspectRatio)

    def set_mode(self, mode):
        self.mode = mode
        self.setDragMode(QGraphicsView.ScrollHandDrag if mode == 'pan' else QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.CrossCursor if mode in ('box', 'teach') else Qt.OpenHandCursor)

    def wheelEvent(self, e):
        f = 1.25 if e.angleDelta().y() > 0 else 0.8
        self.scale(f, f)

    def mousePressEvent(self, e):
        p = self.mapToScene(e.position().toPoint())
        if self.mode == 'box' and e.button() == Qt.LeftButton:
            self._start = p
            self._band = self.scene_.addRect(QRectF(p, p), QPen(QColor('#0a7cff'), 0, Qt.DashLine), QBrush(QColor(10, 124, 255, 30)))
            return
        if self.mode == 'teach' and e.button() == Qt.LeftButton:
            self.clicked.emit(p); return
        if self.mode == 'review' and e.button() == Qt.LeftButton:
            it = self.itemAt(e.position().toPoint())
            while it is not None and it.data(0) is None: it = it.parentItem()
            if it is not None: self.itemPicked.emit(it.data(0)); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._band is not None:
            self._band.setRect(QRectF(self._start, self.mapToScene(e.position().toPoint())).normalized()); return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._band is not None:
            r = self._band.rect(); self.scene_.removeItem(self._band); self._band = None
            if r.width() > 20 and r.height() > 20:
                self.show_region(r); self.regionDrawn.emit(r)
            return
        super().mouseReleaseEvent(e)

    def show_region(self, r):
        if self.region_item is not None: self.scene_.removeItem(self.region_item)
        self.region_item = self.scene_.addRect(r, QPen(QColor('#0a7cff'), 0), QBrush(Qt.NoBrush))
        self.region_item.setZValue(5)

    def clear_overlay(self):
        for it in self.overlay: self.scene_.removeItem(it)
        self.overlay = []
        if self.page_item: self.page_item.setOpacity(1.0)

    def add_marker(self, x, y, color, text='', payload=None, ring=False, r=9):
        pen = QPen(QColor(color), 2 if ring else 1.5)
        it = self.scene_.addEllipse(x - r, y - r, 2 * r, 2 * r, pen, QBrush(Qt.NoBrush))
        it.setZValue(10); it.setData(0, payload); self.overlay.append(it)
        if text:
            t = self.scene_.addSimpleText(text, QFont('Arial', 7)); t.setBrush(QColor(color)); t.setPos(x + r + 1, y - r)
            t.setZValue(10); t.setData(0, payload); self.overlay.append(t)
        return it

    def add_paths(self, polys, color='#333333', width=0):
        path = QPainterPath()
        for pts in polys:
            if len(pts) < 2: continue
            path.moveTo(*pts[0])
            for p in pts[1:]: path.lineTo(*p)
        it = self.scene_.addPath(path, QPen(QColor(color), width)); it.setZValue(6); self.overlay.append(it)
        return it
