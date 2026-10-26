"""让 qfluentwidgets 的遮罩对话框在原生子窗口存在时也能点。

背景：``MaskDialogBase`` 在 ``__init__`` 里 ``setWindowFlags(Qt.FramelessWindowHint)``，
把 ``Qt.Dialog``/``Qt.Window`` 标志丢掉了，所以它其实是主窗口里的一个普通控件
（铺满父窗口的遮罩 + 居中的 ``QFrame``），并不是真正的窗口。

一旦主窗口里出现原生子窗口（桌面分身的画面、原生标题栏都算），会出两个问题：

1. 调 ``QDialog.exec()`` 时平台层把整个主窗口 ``EnableWindow(False)``，遮罩对话框是同一个
   HWND 里的控件，跟着一起失效 —— 对话框看得见、却怎么点都没有反应；
2. 主窗口里的原生子窗口（页面的原生窗口）始终盖在窗口自己画的表面上，鼠标按下去命中的
   是那个原生窗口，事件不会往下发给遮罩对话框里的按钮。

这里对应两处改动：``exec()`` 换成「非模态 show() + 本地 QEventLoop」（视觉、返回值、以及
「点遮罩外没反应」的行为都一样，但不会走到 exec() 的模态分支），并且把对话框本身变成原生
窗口、提到 z 序最上 —— 它本来就是整窗大小的一层遮罩，成为原生窗口之后点击就落在它自己身上，
样式不变。
"""

import contextlib
import ctypes

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets.components.dialog_box.mask_dialog_base import MaskDialogBase

try:
    _user32 = ctypes.WinDLL('user32', use_last_error=True)
except OSError:  # pragma: no cover - 非 Windows 才会走到
    _user32 = None

_HWND_TOP = 0
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010

_installed = False


def _raise_native_window(widget: QWidget) -> None:
    """把控件自己的窗口提到 z 序最上（``raise_()`` 管不到原生子窗口）。"""
    if _user32 is None:
        return
    with contextlib.suppress(RuntimeError):
        handle = int(widget.winId())
        if handle:
            _user32.SetWindowPos(
                ctypes.c_void_p(handle), ctypes.c_void_p(_HWND_TOP),
                0, 0, 0, 0, _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE,
            )


def _prepare_overlay(dialog: QWidget) -> None:
    """让遮罩对话框自己成为原生窗口并置顶，点击才会落在它身上而不是后面那一层。"""
    if not dialog.testAttribute(Qt.WidgetAttribute.WA_NativeWindow):
        dialog.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
    _raise_native_window(dialog)


def _exec_without_modal_block(self) -> int:
    """``MaskDialogBase.exec`` 的替代实现，返回值与 ``QDialog.exec()`` 一致。

    对话框本身就是铺满父窗口的遮罩，show() 之后点击落在遮罩上不会传到后面的
    控件，所以不需要依赖平台的模态禁用。
    """
    if QApplication.instance() is None:
        return int(self.result())

    loop = QEventLoop()
    self.finished.connect(loop.quit)
    try:
        self.show()
        _prepare_overlay(self)
        self.raise_()
        loop.exec()
    finally:
        with contextlib.suppress(RuntimeError, TypeError):
            self.finished.disconnect(loop.quit)

    with contextlib.suppress(RuntimeError):
        return int(self.result())
    return 0


def install_mask_dialog_compat() -> None:
    """装上遮罩对话框的非模态 exec()（重复调用无副作用）。"""
    global _installed
    if _installed:
        return
    _installed = True
    MaskDialogBase.exec = _exec_without_modal_block  # type: ignore[method-assign]
