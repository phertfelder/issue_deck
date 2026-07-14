"""Left navigation rail for the workbench shell.

A fixed-width column of checkable :class:`QToolButton`s driving a sibling
:class:`QStackedWidget`. Buttons are mutually exclusive (a :class:`QButtonGroup`);
clicking one emits :attr:`navigated` with the target stack page index. Items
added after :meth:`add_stretch` are pinned to the bottom (e.g. *Settings*).

Two nav items may point at the same page index while the query/results split is
still pending — that is expected in PR 1 and the button group keeps the checked
state coherent regardless.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .theme import NAV_BUTTON_OBJECT, NAV_RAIL_OBJECT


class NavRail(QWidget):
    """Vertical nav rail; emits :attr:`navigated` (stack page index) on click."""

    navigated = pyqtSignal(int)

    def __init__(self, width: int = 176) -> None:
        super().__init__()
        self.setObjectName(NAV_RAIL_OBJECT)
        self.setFixedWidth(width)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 12, 0, 12)
        self._layout.setSpacing(2)

    def add_item(self, label: str, page_index: int) -> QToolButton:
        """Add a nav button that selects ``page_index`` when clicked.

        A stretch (see :meth:`add_stretch`) inserted first pushes this and later
        items to the bottom, since the layout appends in call order.
        """
        btn = QToolButton()
        btn.setObjectName(NAV_BUTTON_OBJECT)
        btn.setText(label)
        btn.setAccessibleName(f"{label} navigation")
        btn.setCheckable(True)
        btn.setAutoRaise(True)
        # Text-only for PR 1; icons arrive with the Home/Results pages later.
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        # Fill the rail width so hover/checked backgrounds read edge-to-edge.
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.clicked.connect(lambda _=False, idx=page_index: self.navigated.emit(idx))

        self._group.addButton(btn)
        self._layout.addWidget(btn)
        return btn

    def add_stretch(self) -> None:
        """Push subsequently-added items to the bottom of the rail."""
        self._layout.addStretch(1)

    def set_active(self, button: QToolButton) -> None:
        """Check ``button`` without emitting (for initial/programmatic state)."""
        button.setChecked(True)
