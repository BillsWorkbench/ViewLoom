from PySide6.QtWidgets import QGroupBox, QVBoxLayout


def control_group(title):
    box = QGroupBox(title.replace("&", "&&"))
    box.setObjectName("controlGroup")
    layout = QVBoxLayout(box)
    layout.setContentsMargins(12, 20, 12, 12)
    layout.setSpacing(8)
    return box, layout
