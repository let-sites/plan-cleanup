"""A loaded sheet the window can show: a PDF page or a DWG/DXF drawing, with coordinate mapping."""
from __future__ import annotations
import numpy as np
import pymupdf
from PySide6.QtGui import QImage, QPixmap

PX_PER_PT = 2.0   # view resolution for PDF pages

class PdfSheet:
    kind = 'pdf'
    def __init__(self, doc, index):
        self.doc, self.index = doc, index
        self.page = doc[index]
        self.rot = self.page.rotation_matrix          # native -> display
        self.derot = self.page.derotation_matrix      # display -> native
        self._pix = None

    @property
    def title(self):
        return f"Page {self.index + 1}"

    def pixmap(self):
        if self._pix is None:
            pm = self.page.get_pixmap(matrix=pymupdf.Matrix(PX_PER_PT, PX_PER_PT), alpha=False)
            img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format_RGB888).copy()
            self._pix = QPixmap.fromImage(img)
        return self._pix

    # source coordinates are native page points
    def to_view(self, x, y):
        p = pymupdf.Point(x, y) * self.rot
        return p.x * PX_PER_PT, p.y * PX_PER_PT

    def from_view(self, vx, vy):
        p = pymupdf.Point(vx / PX_PER_PT, vy / PX_PER_PT) * self.derot
        return p.x, p.y

    def region_from_view(self, rect):
        a = self.from_view(rect.left(), rect.top()); b = self.from_view(rect.right(), rect.bottom())
        return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))

class DwgSheet:
    kind = 'dwg'
    def __init__(self, doc, name):
        from planclean.dwgin import extents
        self.doc, self.name = doc, name
        self.ext = extents(doc)
        w = self.ext[2] - self.ext[0]; h = self.ext[3] - self.ext[1]
        self.scale = 2400.0 / max(w, h, 1)    # view px per drawing unit
        self._pix = None

    @property
    def title(self):
        return self.name

    def pixmap(self):
        if self._pix is None:
            import matplotlib; matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            from ezdxf.addons.drawing import RenderContext, Frontend
            from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
            w = self.ext[2] - self.ext[0]; h = self.ext[3] - self.ext[1]
            W, H = w * self.scale, h * self.scale
            fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
            ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(self.ext[0], self.ext[2]); ax.set_ylim(self.ext[1], self.ext[3]); ax.axis('off')
            Frontend(RenderContext(self.doc), MatplotlibBackend(ax)).draw_layout(self.doc.modelspace(), finalize=False)
            ax.set_xlim(self.ext[0], self.ext[2]); ax.set_ylim(self.ext[1], self.ext[3])
            fig.canvas.draw()
            buf = np.asarray(fig.canvas.buffer_rgba())
            plt.close(fig)
            img = QImage(buf.data, buf.shape[1], buf.shape[0], buf.strides[0], QImage.Format_RGBA8888).copy()
            self._pix = QPixmap.fromImage(img)
        return self._pix

    def to_view(self, x, y):
        return (x - self.ext[0]) * self.scale, (self.ext[3] - y) * self.scale

    def from_view(self, vx, vy):
        return vx / self.scale + self.ext[0], self.ext[3] - vy / self.scale

    def region_from_view(self, rect):
        a = self.from_view(rect.left(), rect.top()); b = self.from_view(rect.right(), rect.bottom())
        return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
