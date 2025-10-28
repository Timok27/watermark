import sys
import logging
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
    QProgressBar, QSlider, QHBoxLayout, QFileDialog, QComboBox, QMessageBox,
    QSpinBox, QListWidget, QListWidgetItem
)
from PyQt5.QtCore import Qt, QSettings, QThread, pyqtSignal, QFileSystemWatcher
from PyQt5.QtGui import QDragEnterEvent, QDropEvent, QPixmap, QImage, QIcon
from PIL import Image
import cv2
import numpy as np
import mimetypes

logging.basicConfig(
    filename='watermark_app.log', level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s', encoding='utf-8'
)

class ProcessingThread(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(int)
    error = pyqtSignal(str, str)

    def __init__(self, files, app):
        super().__init__()
        self.files = files
        self.app = app

    def run(self):
        total_files = len(self.files)
        for i, file in enumerate(self.files):
            self.progress.emit(i + 1, f"Обработка {file.name} ({i+1}/{total_files})")
            try:
                self.app.process_file(file)
            except Exception as e:
                self.error.emit(file.name, str(e))
        self.finished.emit(total_files)

class WatermarkApp(QWidget):
    def __init__(self):
        super().__init__()
        self.logo_path = None
        self.logo = None
        self.logo_dir = Path(__file__).parent / "Logo"
        self.default_output_folder = Path(__file__).parent / "output"
        self.default_output_folder.mkdir(exist_ok=True)
        self.output_folder = self.default_output_folder
        self.logo_scale = 0.2
        self.logo_alpha = 1.0
        self.available_logos = []
        self._saved_logo_name = None
        self.logo_position = 'center_top'  # options: center_top, center_bottom, top_left, top_right, bottom_left, bottom_right
        self.offset_x = 20
        self.offset_y = 20
        self.files_to_process = []
        self.settings = QSettings("ArtemEdition", "WatermarkApp")
        self.load_settings()
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Watermark Auto — Criga Edition")
        self.setGeometry(400, 200, 500, 500)
        self.setAcceptDrops(True)

        self.setStyleSheet("""
            QWidget { font-size: 14px; }
            QPushButton { padding: 10px; background-color: #4CAF50; color: white; border-radius: 5px; }
            QPushButton:hover { background-color: #45a049; }
            QPushButton:disabled { background-color: #cccccc; }
            QLabel { font-size: 16px; }
            QProgressBar { height: 20px; }
            QComboBox { padding: 5px; background-color: white; }
        """)

        layout = QVBoxLayout()
        self.setLayout(layout)

        self.info_label = QLabel("Перетащи файлы или папку сюда 👇")
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setStyleSheet("border: 2px dashed #aaa; padding: 20px;")

        self.logo_label = QLabel("Логотип: не выбран")
        self.logo_label.setAlignment(Qt.AlignCenter)

        # ComboBox для выбора логотипа
        logo_layout = QHBoxLayout()
        logo_select_label = QLabel("Выбери логотип:")
        logo_select_label.setStyleSheet("background-color: white; padding: 5px;")
        self.logo_combo = QComboBox()
        self.logo_combo.currentIndexChanged.connect(self.load_predefined_logo)
        # Кнопка для обновления списка логотипов
        self.btn_refresh_logos = QPushButton("Обновить")
        self.btn_refresh_logos.clicked.connect(self.refresh_logo_list)
        # Кнопка для выбора логотипа из любой папки
        self.btn_choose_logo = QPushButton("Выбрать файл...")
        self.btn_choose_logo.clicked.connect(self.choose_logo_file)
        logo_layout.addWidget(logo_select_label)
        logo_layout.addWidget(self.logo_combo)
        logo_layout.addWidget(self.btn_refresh_logos)
        logo_layout.addWidget(self.btn_choose_logo)

        # Превью логотипа (больше и контрастнее)
        self.preview_label = QLabel("Превью логотипа")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedSize(200, 200)
        self.preview_label.setStyleSheet("border: 1px solid #ddd; background: #fff;")

        self.progress_bar = QProgressBar()
        self.progress_bar.setAlignment(Qt.AlignCenter)
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)

        btn_output = QPushButton("Выбрать папку для сохранения")
        btn_output.clicked.connect(self.select_output_folder)

        # Очередь файлов (список) для обработки
        queue_layout = QHBoxLayout()
        self.queue_list = QListWidget()
        self.queue_list.setFixedHeight(120)
        btn_remove = QPushButton("Удалить выбранные")
        btn_remove.clicked.connect(self.remove_selected_from_queue)
        queue_layout.addWidget(self.queue_list)
        queue_layout.addWidget(btn_remove)

        # Параметры позиции логотипа
        pos_layout = QHBoxLayout()
        pos_label = QLabel("Позиция:")
        self.pos_combo = QComboBox()
        # отображаемые тексты и реальные значения
        pos_items = [("По центру сверху", 'center_top'), ("По центру снизу", 'center_bottom'),
                     ("Верхний левый", 'top_left'), ("Верхний правый", 'top_right'),
                     ("Нижний левый", 'bottom_left'), ("Нижний правый", 'bottom_right')]
        for text, val in pos_items:
            self.pos_combo.addItem(text, val)
        # выбрать сохранённое значение
        try:
            idx = next(i for i in range(self.pos_combo.count()) if self.pos_combo.itemData(i) == self.logo_position)
        except Exception:
            idx = 0
        self.pos_combo.setCurrentIndex(idx)
        self.pos_combo.currentIndexChanged.connect(lambda i: setattr(self, 'logo_position', self.pos_combo.itemData(i)) or self.save_settings())
        offset_label_x = QLabel("Отступ X:")
        self.offset_x_spin = QSpinBox()
        self.offset_x_spin.setRange(0, 2000)
        self.offset_x_spin.setValue(int(self.offset_x))
        self.offset_x_spin.valueChanged.connect(lambda v: setattr(self, 'offset_x', v) or self.save_settings())
        offset_label_y = QLabel("Y:")
        self.offset_y_spin = QSpinBox()
        self.offset_y_spin.setRange(0, 2000)
        self.offset_y_spin.setValue(int(self.offset_y))
        self.offset_y_spin.valueChanged.connect(lambda v: setattr(self, 'offset_y', v) or self.save_settings())
        pos_layout.addWidget(pos_label)
        pos_layout.addWidget(self.pos_combo)
        pos_layout.addWidget(offset_label_x)
        pos_layout.addWidget(self.offset_x_spin)
        pos_layout.addWidget(offset_label_y)
        pos_layout.addWidget(self.offset_y_spin)

        # Ползунок масштаба
        scale_layout = QHBoxLayout()
        scale_label = QLabel(f"Масштаб логотипа: {int(self.logo_scale*100)}%")
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setMinimum(10)
        self.scale_slider.setMaximum(100)
        self.scale_slider.setValue(int(self.logo_scale*100))
        self.scale_slider.valueChanged.connect(lambda val: self.update_scale(val, scale_label))
        scale_layout.addWidget(scale_label)
        scale_layout.addWidget(self.scale_slider)

        # Ползунок прозрачности
        alpha_layout = QHBoxLayout()
        alpha_label = QLabel(f"Прозрачность логотипа: {int(self.logo_alpha*100)}%")
        self.alpha_slider = QSlider(Qt.Horizontal)
        self.alpha_slider.setMinimum(0)
        self.alpha_slider.setMaximum(100)
        self.alpha_slider.setValue(int(self.logo_alpha*100))
        self.alpha_slider.valueChanged.connect(lambda val: self.update_alpha(val, alpha_label))
        alpha_layout.addWidget(alpha_label)
        alpha_layout.addWidget(self.alpha_slider)

        self.btn_start = QPushButton("Начать обработку")
        self.btn_start.clicked.connect(self.start_processing)
        self.btn_start.setEnabled(False)

        layout.addWidget(self.logo_label)
        layout.addLayout(logo_layout)
        layout.addWidget(self.preview_label)
        layout.addWidget(btn_output)
        layout.addLayout(queue_layout)
        layout.addLayout(scale_layout)
        layout.addLayout(alpha_layout)
        layout.addWidget(self.btn_start)
        layout.addWidget(self.info_label)
        layout.addWidget(self.progress_bar)

        # Заполнить список логотипов из папки Logo и попытаться восстановить выбор
        # Наблюдатель папки Logo для авто-обновления
        if not self.logo_dir.exists():
            try:
                self.logo_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logging.error(f"Не удалось создать папку Logo: {e}")
        self.watcher = QFileSystemWatcher()
        try:
            self.watcher.addPath(str(self.logo_dir))
            self.watcher.directoryChanged.connect(self.refresh_logo_list)
        except Exception:
            # некоторые окружения не поддерживают watcher на директорию; он не критичен
            logging.info("QFileSystemWatcher: не удалось добавить наблюдение за папкой Logo")

        self.refresh_logo_list()
        # Если есть сохранённое имя логотипа — выбрать его
        if self._saved_logo_name:
            idx = next((i for i, p in enumerate(self.available_logos) if p.name == self._saved_logo_name), None)
            if idx is not None:
                self.logo_combo.setCurrentIndex(idx)
        # Если есть сохранённый путь и он валиден — загрузим логотип
        if self.logo_path:
            if Path(self.logo_path).exists():
                self.load_logo_from_path(self.logo_path)
            else:
                # попробовать найти по имени в папке Logo
                if self._saved_logo_name:
                    alt = self.logo_dir / self._saved_logo_name
                    if alt.exists():
                        self.load_logo_from_path(str(alt))

    def load_predefined_logo(self, index):
        # Загружает логотип по индексу из self.available_logos
        if index == -1 or not getattr(self, 'available_logos', None):
            return
        if index < 0 or index >= len(self.available_logos):
            return
        path = self.available_logos[index]
        if not path.exists():
            self.info_label.setText(f"Логотип {path.name} не найден!")
            logging.error(f"Логотип не найден: {path}")
            return
        self.load_logo_from_path(str(path))

    def refresh_logo_list(self):
        """Сканирует папку Logo и заполняет ComboBox списком доступных файлов."""
        self.available_logos = []
        if not self.logo_dir.exists():
            try:
                self.logo_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logging.error(f"Не удалось создать папку Logo: {e}")
                return
        exts = {'.png', '.jpg', '.jpeg', '.webp'}
        for p in sorted(self.logo_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in exts:
                self.available_logos.append(p)
        self.logo_combo.blockSignals(True)
        self.logo_combo.clear()
        if not self.available_logos:
            self.logo_combo.addItem("(нет логотипов)")
            self.logo_combo.setEnabled(False)
        else:
            # добавим миниатюры в ComboBox
            for p in self.available_logos:
                try:
                    pix = QPixmap(str(p))
                    icon = QIcon(pix.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                except Exception:
                    icon = QIcon()
                self.logo_combo.addItem(icon, p.stem)
            self.logo_combo.setEnabled(True)
        self.logo_combo.blockSignals(False)

    def load_logo_from_path(self, path):
        self.logo_path = path
        self.logo_label.setText(f"Логотип: {Path(path).name}")
        try:
            self.logo = Image.open(self.logo_path).convert("RGBA")
            self.info_label.setText("Теперь перетащи файлы или папку для обработки")
            self.save_settings()
            self.update_preview()
            self.btn_start.setEnabled(bool(self.files_to_process))
            logging.info(f"Логотип загружен: {path}")
        except Exception as e:
            logging.error(f"Ошибка загрузки логотипа: {str(e)}")
            self.info_label.setText(f"Ошибка загрузки логотипа: {str(e)}")
            self.logo_path = None
            self.logo = None
            self.preview_label.clear()

    def choose_logo_file(self):
        file_filter = "Изображения (*.png *.jpg *.jpeg *.webp)"
        fn, _ = QFileDialog.getOpenFileName(self, "Выбери файл логотипа", str(self.logo_dir), file_filter)
        if fn:
            self.load_logo_from_path(fn)

    def remove_selected_from_queue(self):
        items = self.queue_list.selectedItems()
        for it in items:
            path = it.data(Qt.UserRole)
            try:
                p = Path(path)
                if p in self.files_to_process:
                    self.files_to_process.remove(p)
            except Exception:
                pass
            self.queue_list.takeItem(self.queue_list.row(it))

    def update_preview(self):
        if self.logo:
            try:
                img = self.logo.copy().convert("RGB")
                data = np.array(img)
                qimg = QImage(data.data, data.shape[1], data.shape[0], data.strides[0], QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(qimg)
                w = self.preview_label.width()
                h = self.preview_label.height()
                self.preview_label.setPixmap(pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            except Exception as e:
                logging.error(f"Ошибка отображения превью: {str(e)}")
                self.preview_label.setText("Ошибка превью")

    def update_scale(self, val, label):
        self.logo_scale = val / 100
        label.setText(f"Масштаб логотипа: {val}%")
        self.save_settings()

    def update_alpha(self, val, label):
        self.logo_alpha = val / 100
        label.setText(f"Прозрачность логотипа: {val}%")
        self.save_settings()

    def load_settings(self):
        # Загружаем сохранённые настройки; сохраняем имя логотипа отдельно
        self.logo_path = self.settings.value("logo_path", None)
        self._saved_logo_name = self.settings.value("logo_name", None)
        # позиция и отступы
        self.logo_position = self.settings.value("logo_position", self.logo_position)
        try:
            self.offset_x = int(self.settings.value("offset_x", self.offset_x))
            self.offset_y = int(self.settings.value("offset_y", self.offset_y))
        except Exception:
            pass
        saved_output_folder = self.settings.value("output_folder", None)
        if saved_output_folder and Path(saved_output_folder).exists():
            self.output_folder = Path(saved_output_folder)
        else:
            self.output_folder = self.default_output_folder
        self.logo_scale = float(self.settings.value("logo_scale", 0.2))
        self.logo_alpha = float(self.settings.value("logo_alpha", 1.0))
        # combo index восстановим после заполнения списка (если понадобится)
        logging.info("Настройки загружены")

    def save_settings(self):
        self.settings.setValue("logo_path", self.logo_path if self.logo_path else "")
        # сохраним имя логотипа (файл) для устойчивого восстановления
        try:
            if self.logo_path:
                self.settings.setValue("logo_name", Path(self.logo_path).name)
            else:
                self.settings.setValue("logo_name", "")
        except Exception:
            pass
        self.settings.setValue("output_folder", str(self.output_folder))
        self.settings.setValue("logo_scale", self.logo_scale)
        self.settings.setValue("logo_alpha", self.logo_alpha)
        # позиция логотипа и отступы
        try:
            self.settings.setValue("logo_position", self.logo_position)
            self.settings.setValue("offset_x", int(self.offset_x))
            self.settings.setValue("offset_y", int(self.offset_y))
        except Exception:
            pass
        if hasattr(self, 'logo_combo'):
            self.settings.setValue("logo_combo_index", self.logo_combo.currentIndex())
        logging.info("Настройки сохранены")

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выбери папку для сохранения")
        if folder:
            self.output_folder = Path(folder)
            self.output_folder.mkdir(exist_ok=True)
            self.info_label.setText(f"Папка для сохранения: {self.output_folder}")
            self.save_settings()
            logging.info(f"Папка для сохранения изменена: {folder}")
        else:
            self.output_folder = self.default_output_folder
            self.info_label.setText(f"Папка для сохранения: {self.output_folder}")
            self.save_settings()
            logging.info(f"Папка для сохранения сброшена на значение по умолчанию: {self.default_output_folder}")

    def start_processing(self):
        if not self.logo:
            self.info_label.setText("Сначала выбери логотип!")
            return
        if not self.files_to_process:
            self.info_label.setText("Нет файлов для обработки!")
            return
        paths = self.files_to_process
        self.files_to_process = []
        self.process_paths(paths)
        self.btn_start.setEnabled(False)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        if not self.logo:
            self.info_label.setText("Сначала выбери логотип!")
            logging.warning("Попытка обработки без логотипа")
            return
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        for p in paths:
            self.files_to_process.append(p)
            try:
                item = QListWidgetItem(p.name)
                item.setData(Qt.UserRole, str(p))
                self.queue_list.addItem(item)
            except Exception:
                pass
        self.info_label.setText(f"Добавлено {len(paths)} элементов для обработки")
        self.btn_start.setEnabled(True)

    def process_paths(self, paths):
        files = []
        supported_extensions = {'.jpg', '.jpeg', '.png', '.mp4', '.mov', '.avi', '.mkv', '.webm', '.wmv'}
        for path in paths:
            if path.is_dir():
                files.extend([
                    f for f in path.rglob('*') if f.is_file() and
                    (f.suffix.lower() in supported_extensions or
                     mimetypes.guess_type(f)[0] and mimetypes.guess_type(f)[0].startswith(('image/', 'video/')))
                ])
            elif path.is_file() and (path.suffix.lower() in supported_extensions or
                                     mimetypes.guess_type(path)[0] and mimetypes.guess_type(path)[0].startswith(('image/', 'video/'))):
                files.append(path)
        self.process_files(files)

    def process_files(self, files):
        total_files = len(files)
        if total_files == 0:
            self.info_label.setText("Нет подходящих файлов для обработки")
            logging.info("Нет подходящих файлов для обработки")
            return
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(total_files)
        self.progress_bar.setValue(0)
        self.thread = ProcessingThread(files, self)
        self.thread.progress.connect(self.update_progress)
        self.thread.finished.connect(self.on_processing_finished)
        self.thread.error.connect(self.show_error)
        self.thread.start()

    def update_progress(self, value, text):
        self.progress_bar.setValue(value)
        self.info_label.setText(text)
        QApplication.processEvents()

    def on_processing_finished(self, total_files):
        self.progress_bar.setVisible(False)
        self.info_label.setText(f"✅ Готово! Файлы сохранены в {self.output_folder}")
        logging.info(f"Обработано {total_files} файлов")
        self.btn_start.setEnabled(bool(self.logo and self.files_to_process))

    def show_error(self, file_name, error_msg):
        QMessageBox.critical(self, "Ошибка", f"Не удалось обработать {file_name}: {error_msg}")

    def get_unique_output_path(self, output_path: Path):
        if not output_path.exists():
            return output_path
        base, ext = output_path.stem, output_path.suffix
        counter = 1
        while True:
            new_path = output_path.with_name(f"{base}_watermarked_{counter}{ext}")
            if not new_path.exists():
                return new_path
            counter += 1

    def process_file(self, file_path: Path):
        ext = file_path.suffix.lower()
        output_file = self.get_unique_output_path(self.output_folder / file_path.name)
        logging.info(f"Обработка файла: {file_path} -> {output_file}")
        if ext in [".jpg", ".jpeg", ".png"]:
            self.add_watermark_image(file_path, output_file)
            logging.info(f"Изображение успешно обработано: {output_file}")
        elif ext in [".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv"]:
            self.add_watermark_video(file_path, output_file)
            logging.info(f"Видео успешно обработано: {output_file}")

    def add_watermark_image(self, image_path, output_path):
        base = Image.open(image_path).convert("RGBA")
        logo_resized = self.logo.copy()
        scale = min(base.width * self.logo_scale / self.logo.width, base.height * self.logo_scale / self.logo.height, 1)
        logo_resized = logo_resized.resize((int(self.logo.width * scale), int(self.logo.height * scale)), Image.LANCZOS)
        alpha = logo_resized.split()[3].point(lambda p: int(p * self.logo_alpha))
        logo_resized.putalpha(alpha)
        w, h = base.size
        lw, lh = logo_resized.size
        # вычисляем позицию в зависимости от настроек
        pos = self.logo_position
        if pos == 'center_top':
            x = (w - lw) // 2
            y = int(self.offset_y)
        elif pos == 'center_bottom':
            x = (w - lw) // 2
            y = h - lh - int(self.offset_y)
        elif pos == 'top_left':
            x = int(self.offset_x)
            y = int(self.offset_y)
        elif pos == 'top_right':
            x = w - lw - int(self.offset_x)
            y = int(self.offset_y)
        elif pos == 'bottom_left':
            x = int(self.offset_x)
            y = h - lh - int(self.offset_y)
        elif pos == 'bottom_right':
            x = w - lw - int(self.offset_x)
            y = h - lh - int(self.offset_y)
        else:
            x = (w - lw) // 2
            y = int(self.offset_y)
        position = (x, y)
        base.paste(logo_resized, position, logo_resized)
        if image_path.suffix.lower() == ".png":
            base.save(output_path, "PNG")
        else:
            base.convert("RGB").save(output_path, "JPEG", quality=95)

    def add_watermark_video(self, video_path, output_path):
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise ValueError(f"Не удалось открыть видео: {video_path}")
        fourcc = cv2.VideoWriter_fourcc(*'H264')
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        out = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))
        if not out.isOpened():
            cap.release()
            raise ValueError(f"Не удалось создать выходное видео: {output_path}")

        logo = cv2.imread(str(self.logo_path), cv2.IMREAD_UNCHANGED)
        if logo is None:
            raise ValueError(f"Не удалось загрузить логотип: {self.logo_path}")
        if logo.shape[2] == 3:
            b_channel, g_channel, r_channel = cv2.split(logo)
            alpha_channel = np.ones(b_channel.shape, dtype=b_channel.dtype) * 255
            logo = cv2.merge((b_channel, g_channel, r_channel, alpha_channel))
        scale = min(width * self.logo_scale / logo.shape[1], height * self.logo_scale / logo.shape[0], 1)
        new_w = int(logo.shape[1] * scale)
        new_h = int(logo.shape[0] * scale)
        logo_resized = cv2.resize(logo, (new_w, new_h), interpolation=cv2.INTER_AREA)
        logo_resized[:, :, 3] = (logo_resized[:, :, 3].astype(float) * self.logo_alpha).astype(np.uint8)
        # позиция логотипа для видео
        pos = self.logo_position
        if pos == 'center_top':
            x_offset = (width - new_w) // 2
            y_offset = int(self.offset_y)
        elif pos == 'center_bottom':
            x_offset = (width - new_w) // 2
            y_offset = height - new_h - int(self.offset_y)
        elif pos == 'top_left':
            x_offset = int(self.offset_x)
            y_offset = int(self.offset_y)
        elif pos == 'top_right':
            x_offset = width - new_w - int(self.offset_x)
            y_offset = int(self.offset_y)
        elif pos == 'bottom_left':
            x_offset = int(self.offset_x)
            y_offset = height - new_h - int(self.offset_y)
        elif pos == 'bottom_right':
            x_offset = width - new_w - int(self.offset_x)
            y_offset = height - new_h - int(self.offset_y)
        else:
            x_offset = (width - new_w) // 2
            y_offset = int(self.offset_y)

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame.shape[2] == 3:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
            alpha_logo = logo_resized[:, :, 3] / 255.0
            for c in range(0, 3):
                frame[y_offset:y_offset+new_h, x_offset:x_offset+new_w, c] = \
                    (alpha_logo * logo_resized[:, :, c] + (1 - alpha_logo) * frame[y_offset:y_offset+new_h, x_offset:x_offset+new_w, c])
            out.write(cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR))

        cap.release()
        out.release()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = WatermarkApp()
    window.show()
    sys.exit(app.exec_())