"""Zoomable sheet view with three modes: pan, draw a box, click a symbol."""
from __future__ import annotations
from PySide6.QtCore import Qt, QRectF, Signal, QPointF
from PySide6.QtGui import QPen, QColor, QBrush, QPainterPath, QPainter, QFont
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsRectItem

CLASS_COLORS = {
    'outlet': '#d62728', 'outlet_240': '#8c1c13', 'switch': '#1f77b4', 'dimmer': '#1f77b4', 'ceiling_light': '#9467bd', 'wall_light': '#ff7f0e',
    'recessed_can': '#2ca02c', 'disk_light': '#8c564b', 'fan': '#17becf', 'exhaust_fan': '#7f7f7f', 'smoke': '#bcbd22',
    'co_smoke': '#bcbd22', 'ceiling_outlet': '#e377c2', 'floor_outlet': '#e377c2', 'flood_light': '#ff7f0e',
}

class SheetView(QGraphicsView):
    regionDrawn = Signal(QRectF)
    clicked = Signal(QPointF)
    exampleMoved = Signal(str, QPointF)      # a taught symbol's marker dragged to a new centre
    examplePicked = Signal(str)              # a taught symbol's marker clicked
    exampleResized = Signal(str, int)        # scrolled over a taught symbol: +1 bigger, -1 smaller
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
        self._zb = None; self._zoom_buttons()
        self.ex_markers = {}; self._drag = None; self.viewport().setMouseTracking(True)

    def set_sheet(self, pixmap):
        # Take every item out one by one rather than scene.clear(): clear() deletes the C++ items under
        # Python's feet, and dropping a stale reference later (the blue box) crashed the app.
        for it in self.scene_.items():
            if it.parentItem() is None: self.scene_.removeItem(it)
        self.overlay = []; self.region_item = None; self._band = None; self.page_item = None; self.done_items = []
        self.ex_markers = {}; self._drag = None
        self.page_item = self.scene_.addPixmap(pixmap)
        self.scene_.setSceneRect(QRectF(pixmap.rect()))
        self.fitInView(self.page_item, Qt.KeepAspectRatio)

    def set_mode(self, mode):
        self.mode = mode
        # on the cleaned plan, dragging empty space moves the page; clicking a symbol selects it
        self.setDragMode(QGraphicsView.ScrollHandDrag if mode in ('pan', 'review') else QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.CrossCursor if mode in ('box', 'teach') else Qt.OpenHandCursor)

    def _zoom_buttons(self):
        from PySide6.QtWidgets import QWidget, QHBoxLayout, QToolButton
        w = QWidget(self); w.setObjectName('zoombar')
        w.setStyleSheet('#zoombar{background:white;border:1px solid #dfe3e7;border-radius:8px}'
                        'QToolButton{border:none;padding:4px 10px;font-size:16px;font-weight:600;color:#1f2a33}'
                        'QToolButton:hover{background:#edf3f7;border-radius:6px}')
        h = QHBoxLayout(w); h.setContentsMargins(4, 2, 4, 2); h.setSpacing(2)
        for text, tip, fn in (('−', 'Zoom out', lambda: self.zoom(0.8)), ('+', 'Zoom in', lambda: self.zoom(1.25)),
                              ('Fit', 'Show the whole sheet', self.fit)):
            b = QToolButton(); b.setText(text); b.setToolTip(tip); b.clicked.connect(fn); h.addWidget(b)
        w.adjustSize(); self._zb = w

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, '_tr', None) is not None:
            self._tr.adjustSize(); self._tr.move(self.viewport().width() - self._tr.width() - 14, 14)
        if getattr(self, '_zb', None) is not None:
            self._zb.move(self.viewport().width() - self._zb.width() - 14, self.viewport().height() - self._zb.height() - 14)

    def set_corner_widget(self, w):
        """A widget pinned to the top-right corner of the drawing (the Original on/off button)."""
        w.setParent(self); self._tr = w; w.show()
        w.adjustSize(); w.move(self.viewport().width() - w.width() - 14, 14)

    def zoom(self, f, at=None):
        if at is not None:
            self.setTransformationAnchor(QGraphicsView.NoAnchor)
            old = self.mapToScene(at.toPoint()) if hasattr(at, 'toPoint') else self.mapToScene(at)
            self.scale(f, f); self.centerOn(old)
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        else:
            self.setTransformationAnchor(QGraphicsView.AnchorViewCenter); self.scale(f, f)
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)

    def fit(self):
        target = self.region_item if (self.region_item is not None and self.mode == 'review') else self.page_item
        if target is not None: self.fitInView(target, Qt.KeepAspectRatio)

    def mouseDoubleClickEvent(self, e):
        # double-click zooms in on that spot (Shift + double-click zooms out)
        if self.mode in ('box', 'pan', 'review') and e.button() == Qt.LeftButton:
            self.zoom(0.5 if e.modifiers() & Qt.ShiftModifier else 2.0, e.position()); return
        super().mouseDoubleClickEvent(e)

    def wheelEvent(self, e):
        # trackpad two-finger scroll moves around; a mouse wheel (or Ctrl/Cmd + scroll) zooms
        if not e.pixelDelta().isNull() and not (e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier)):
            d = e.pixelDelta()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - d.y()); return
        dy = e.angleDelta().y() or e.pixelDelta().y()
        if dy == 0: return
        f = 1.25 if dy > 0 else 0.8
        self.scale(f, f)

    def event(self, e):
        # trackpad pinch to zoom (macOS)
        if e.type() == e.Type.NativeGesture and e.gestureType() == Qt.ZoomNativeGesture:
            f = 1.0 + e.value(); self.scale(f, f); return True
        return super().event(e)

    def _start_pan(self, pos):
        self._pan = pos; self.viewport().setCursor(Qt.ClosedHandCursor)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = True; self.viewport().setCursor(Qt.OpenHandCursor); return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = False; self._pan = None; self.set_mode(self.mode); return
        super().keyReleaseEvent(e)

    def mousePressEvent(self, e):
        p = self.mapToScene(e.position().toPoint())
        if e.button() == Qt.MiddleButton or (getattr(self, '_space', False) and e.button() == Qt.LeftButton):
            self._start_pan(e.position()); return
        if self.mode == 'box' and e.button() == Qt.LeftButton:
            self._start = p
            pen = QPen(QColor('#2f7fb0'), 2.5, Qt.DashLine); pen.setCosmetic(True)
            self._band = self.scene_.addRect(QRectF(p, p), pen, QBrush(QColor(74, 144, 184, 35)))
            return
        if self.mode == 'teach' and e.button() == Qt.LeftButton:
            m = self._ex_at(e.position().toPoint())
            if m is not None:                                    # grab a taught symbol
                self._drag = (m, p, m.pos(), False); self.viewport().setCursor(Qt.ClosedHandCursor); return
            self.clicked.emit(p); return
        if self.mode == 'review' and e.button() == Qt.LeftButton:
            it = self.itemAt(e.position().toPoint())
            while it is not None and it.data(0) is None: it = it.parentItem()
            if it is not None: self.itemPicked.emit(it.data(0)); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if getattr(self, '_pan', None) is not None:
            d = e.position() - self._pan; self._pan = e.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y())); return
        if self._band is not None:
            self._band.setRect(QRectF(self._start, self.mapToScene(e.position().toPoint())).normalized()); return
        if self._drag is not None:
            m, start, pos0, _ = self._drag; p = self.mapToScene(e.position().toPoint())
            m.setPos(pos0 + (p - start)); self._drag = (m, start, pos0, True); return
        if self.mode == 'teach':
            self.viewport().setCursor(Qt.OpenHandCursor if self._ex_at(e.position().toPoint()) is not None else Qt.CrossCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            m, start, pos0, moved = self._drag; self._drag = None
            self.viewport().setCursor(Qt.OpenHandCursor)
            if moved and (m.pos() - pos0).manhattanLength() > 0.5: self.exampleMoved.emit(m.data(1), m.pos())
            else: self.examplePicked.emit(m.data(1))
            return
        if getattr(self, '_pan', None) is not None:
            self._pan = None; self.set_mode(self.mode); return
        if self._band is not None:
            r = self._band.rect(); self.scene_.removeItem(self._band); self._band = None
            if r.width() > 20 and r.height() > 20:
                self.show_region(r); self.regionDrawn.emit(r)
            elif r.width() < 3 and r.height() < 3:
                self.zoom(2.0, e.position())        # a plain click zooms in on that spot
            return
        super().mouseReleaseEvent(e)

    def show_region(self, r):
        if self.region_item is not None and self.region_item.scene() is self.scene_:
            self.region_item.setRect(r); return
        pen = QPen(QColor('#2f7fb0'), 2.5); pen.setCosmetic(True)       # same weight at any zoom
        self.region_item = self.scene_.addRect(r, pen, QBrush(QColor(74, 144, 184, 18)))
        self.region_item.setZValue(5)

    def show_done(self, boxes):
        """Outline areas already added to the plan: (rect, label) pairs."""
        for it in getattr(self, 'done_items', []):
            if it.scene() is self.scene_: self.scene_.removeItem(it)
        self.done_items = []
        pen = QPen(QColor('#3c9a5f'), 2); pen.setCosmetic(True)
        for r, label in boxes:
            it = self.scene_.addRect(r, pen, QBrush(QColor(60, 154, 95, 28))); it.setZValue(4); self.done_items.append(it)
            t = self.scene_.addSimpleText('✓ ' + label, QFont('Arial', 11, QFont.Bold)); t.setBrush(QColor('#2e7d4c'))
            t.setFlag(t.GraphicsItemFlag.ItemIgnoresTransformations); t.setPos(r.left() + 4, r.top() + 4); t.setZValue(4)
            self.done_items.append(t)

    def add_ex_marker(self, eid, x, y, color, label, r=9):
        """A taught symbol on the plan: the box it captured. Hover shows a hand; drag it to re-centre it."""
        self.remove_ex_marker(eid)
        pen = QPen(QColor(color), 2); pen.setCosmetic(True)
        ring = self.scene_.addRect(-r, -r, 2 * r, 2 * r, pen, QBrush(QColor(46, 158, 91, 30)))
        cross = QPen(QColor(color), 1); cross.setCosmetic(True)
        c = max(3, r * 0.4)
        for a, b in (((-c, 0), (c, 0)), ((0, -c), (0, c))):
            l = self.scene_.addLine(a[0], a[1], b[0], b[1], cross); l.setParentItem(ring)
        t = self.scene_.addSimpleText(label, QFont('Arial', 7)); t.setBrush(QColor(color)); t.setPos(r + 1, -r); t.setParentItem(ring)
        ring.setPos(x, y); ring.setZValue(12); ring.setData(1, eid); ring.setToolTip('Drag to re-centre this taught symbol')
        self.ex_markers[eid] = ring

    def remove_ex_marker(self, eid=None):
        for k in ([eid] if eid else list(self.ex_markers)):
            it = self.ex_markers.pop(k, None)
            if it is not None and it.scene() is self.scene_: self.scene_.removeItem(it)

    def select_ex_marker(self, eid):
        for k, it in self.ex_markers.items():
            p = it.pen(); p.setColor(QColor('#e0a100' if k == eid else '#2e9e5b')); p.setWidthF(3 if k == eid else 2); it.setPen(p)

    def _ex_at(self, pos):
        for it in self.items(pos):
            while it is not None and it.data(1) is None: it = it.parentItem()
            if it is not None and it.data(1) in self.ex_markers: return it
        return None

    def clear_overlay(self):
        for it in self.overlay:
            if it.scene() is self.scene_: self.scene_.removeItem(it)
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

    def add_paths(self, polys, color='#333333', width=0, fill=False):
        path = QPainterPath()
        for pts in polys:
            if len(pts) < 2: continue
            path.moveTo(*pts[0])
            for p in pts[1:]: path.lineTo(*p)
            if fill: path.closeSubpath()
        it = self.scene_.addPath(path, QPen(QColor(color), width), QBrush(QColor(color)) if fill else QBrush(Qt.NoBrush))
        it.setZValue(6); self.overlay.append(it)
        return it
