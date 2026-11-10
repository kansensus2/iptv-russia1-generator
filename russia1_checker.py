import sys
import asyncio
import os
import ctypes
import subprocess
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QTextEdit, QProgressBar, QListWidget, QGroupBox
)
import aiohttp

TARGET_SOURCES = [
    {
        "name": "Free-IPTV Russia Catalog",
        "url": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/ru.m3u",
    }
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8"
}


class StreamCheckerWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(list)

    def __init__(self, usb_path):
        super().__init__()
        self.usb_path = usb_path

    def run(self):
        asyncio.run(self.async_main())

    async def async_main(self):
        self.log_signal.emit("[+] Инициализация асинхронного сканера...")

        connector = aiohttp.TCPConnector(ssl=False)

        async with aiohttp.ClientSession(connector=connector) as session:
            candidates = []
            for source in TARGET_SOURCES:
                self.log_signal.emit(f"[+] Сканирование источника: {source['name']}...")
                try:
                    async with session.get(source["url"], headers=HEADERS,
                                           timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            content = await resp.text()
                            lines = content.splitlines()
                            for i, line in enumerate(lines):
                                low_line = line.lower()
                                if ("росси" in low_line or "russia" in low_line) and "1" in low_line:
                                    if i + 1 < len(lines) and lines[i + 1].startswith("http"):
                                        url = lines[i + 1].strip()
                                        if url not in candidates:
                                            candidates.append(url)
                except Exception as e:
                    self.log_signal.emit(f"[!] Ошибка загрузки каталога: {e}")

            self.log_signal.emit(f"[+] Найдено кандидатов для проверки: {len(candidates)}")
            if not candidates:
                self.log_signal.emit("[!] Не найдено подходящих потоков в плейлисте.")
                self.finished_signal.emit([])
                return

            tasks = [self.check_stream(session, url, idx + 1) for idx, url in enumerate(candidates[:20])]
            results = await asyncio.gather(*tasks)

            valid_results = [r for r in results if r and r["status"] == "ДОСТУПЕН"]
            self.log_signal.emit(f"\n[+] Проверка завершена. Рабочих потоков: {len(valid_results)}")

            if self.usb_path and valid_results:
                self.save_to_usb(valid_results)

            self.finished_signal.emit(valid_results)

    async def check_stream(self, session, url, idx):
        start_time = asyncio.get_event_loop().time()
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True) as response:
                latency = round((asyncio.get_event_loop().time() - start_time) * 1000, 2)
                if response.status == 200:
                    text = await response.text()
                    if "#EXTM3U" in text or "m3u8" in response.headers.get("Content-Type",
                                                                           "") or "ts" in url or "m3u8" in url:
                        self.log_signal.emit(f"✅ Поток #{idx} ДОСТУПЕН ({latency} ms)")
                        return {"url": url, "latency": latency, "status": "ДОСТУПЕН"}
        except Exception:
            pass
        self.log_signal.emit(f"❌ Поток #{idx} недоступен")
        return None

    def save_to_usb(self, valid_results):
        try:
            filepath = os.path.join(self.usb_path, "russia1_working.m3u")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for idx, res in enumerate(valid_results, 1):
                    f.write(
                        f'#EXTINF:-1 tvg-name="Russia 1" group-title="TV Playlist",Россия 1 (Канал #{idx} - {res["latency"]}ms)\n')
                    f.write(f"{res['url']}\n")
            self.log_signal.emit(f"[+] Плейлист для телевизора успешно записан: {filepath}")
        except Exception as e:
            self.log_signal.emit(f"[!] Ошибка записи на флешку: {e}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Генератор IPTV 'Россия 1' для Телевизора")
        self.resize(850, 750)

        self.init_ui()
        # Автоматическое сканирование только USB-флешек при запуске
        self.refresh_usb_drives()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Блок настройки флешки для телевизора
        tv_group = QGroupBox("📺 Запись на USB-флешку для телевизора")
        tv_layout = QVBoxLayout(tv_group)

        label_info = QLabel("Обнаруженная USB-флешка для сохранения файла 'russia1_working.m3u':")
        label_info.setStyleSheet("color: #555; font-size: 11px;")
        tv_layout.addWidget(label_info)

        drive_layout = QHBoxLayout()
        self.combo_drives = QComboBox()
        btn_refresh = QPushButton("Обновить флешки")
        btn_refresh.clicked.connect(self.refresh_usb_drives)
        drive_layout.addWidget(self.combo_drives, 1)
        drive_layout.addWidget(btn_refresh)
        tv_layout.addLayout(drive_layout)

        main_layout.addWidget(tv_group)

        # Кнопка запуска поиска
        self.btn_start = QPushButton("Найти рабочие потоки и записать на флешку")
        self.btn_start.setStyleSheet(
            "background-color: #2ecc71; color: white; font-weight: bold; font-size: 14px; padding: 10px;")
        self.btn_start.clicked.connect(self.start_process)
        main_layout.addWidget(self.btn_start)

        # Список найденных рабочих потоков
        label_streams = QLabel("Найденные рабочие потоки (нажмите, чтобы открыть в плеере ПК):")
        main_layout.addWidget(label_streams)

        self.list_streams = QListWidget()
        self.list_streams.itemClicked.connect(self.play_stream)
        main_layout.addWidget(self.list_streams, 1)

        # Лог выполнения
        self.text_log = QTextEdit()
        self.text_log.setMaximumHeight(120)
        self.text_log.setReadOnly(True)
        main_layout.addWidget(self.text_log)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

        # Инструкция по воспроизведению с флешки на телевизоре
        help_group = QGroupBox("💡 Инструкция: Как открыть плейлист на телевизоре")
        help_layout = QVBoxLayout(help_group)

        help_text = QLabel(
            "1. Убедитесь, что флешка отформатирована в FAT32 или exFAT (NTFS телевизоры часто не видят).\n"
            "2. Убедитесь, что файл плейлиста 'russia1_working.m3u' находится **в корне флешки**.\n"
            "3. Вставьте флешку в USB-порт телевизора.\n"
            "4. Откройте IPTV-плеер на Smart TV (например, IPTV Pro, TiviMate, VLC for Android) или штатный медиаплеер телевизора.\n"
            "5. Выберите пункт «Открыть локальный файл M3U» или найдите файл через проводник и запустите его."
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet(
            "color: #333; font-size: 11px; background-color: #f9f9f9; padding: 8px; border-radius: 4px;")
        help_layout.addWidget(help_text)

        main_layout.addWidget(help_group)

    def refresh_usb_drives(self):
        self.combo_drives.clear()

        if os.name == 'nt':
            # Проверяем диски через Windows API на предмет съемных накопителей (DRIVE_REMOVABLE = 2)
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for i in range(26):
                if bitmask & (1 << i):
                    drive_letter = f"{chr(65 + i)}:\\"
                    drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive_letter)
                    # 2 означает DRIVE_REMOVABLE (съемный накопитель / флешка)
                    if drive_type == 2:
                        self.combo_drives.addItem(f"USB-флешка ({drive_letter})")
        else:
            # Для Linux проверяем типичные точки монтирования съемных носителей
            for base in ["/media", "/mnt"]:
                if os.path.exists(base):
                    for name in os.listdir(base):
                        full_path = os.path.join(base, name)
                        if os.path.isdir(full_path):
                            self.combo_drives.addItem(full_path)

        if self.combo_drives.count() > 0:
            self.combo_drives.setCurrentIndex(0)
            selected = self.combo_drives.currentText()
            self.text_log.append(f"[+] Автоматически обнаружена USB-флешка: {selected}")
        else:
            self.combo_drives.addItem("USB-флешки не найдены (вставьте флешку и нажмите 'Обновить')")
            self.text_log.append("[!] Съемные USB-накопители не обнаружены. Пожалуйста, вставьте флешку в ПК.")

    def start_process(self):
        selected_text = self.combo_drives.currentText()
        usb_path = None

        if "USB-флешка" in selected_text:
            usb_path = selected_text.split("(")[1].split(")")[0]
        elif os.path.exists(selected_text):
            usb_path = selected_text

        if not usb_path or not os.path.exists(usb_path):
            self.text_log.append(
                "[!] Ошибка: Не выбрана или недоступна USB-флешка! Вставьте накопитель и нажмите 'Обновить флешки'.")
            return

        self.btn_start.setEnabled(False)
        self.combo_drives.setEnabled(False)
        self.progress_bar.show()
        self.text_log.clear()
        self.list_streams.clear()

        self.worker = StreamCheckerWorker(usb_path)
        self.worker.log_signal.connect(self.append_log)
        self.worker.finished_signal.connect(self.process_finished)
        self.worker.start()

    def append_log(self, text):
        self.text_log.append(text)

    def process_finished(self, valid_results):
        self.btn_start.setEnabled(True)
        self.combo_drives.setEnabled(True)
        self.progress_bar.hide()
        self.text_log.append(f"\n=== Готово! Сохранено рабочих потоков: {len(valid_results)} ===")

        self.list_streams.clear()
        for idx, res in enumerate(valid_results, 1):
            item_text = f"Поток #{idx} ({res['latency']} ms) — {res['url']}"
            self.list_streams.addItem(item_text)

    def play_stream(self, item):
        text = item.text()
        url = text.split(" — ")[1]
        self.text_log.append(f"[>] Запуск потока в плеере: {url}")

        try:
            if os.name == 'nt':
                vlc_path = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
                if os.path.exists(vlc_path):
                    subprocess.Popen([vlc_path, url])
                else:
                    subprocess.Popen(["vlc", url])
            else:
                subprocess.Popen(["vlc", url])
        except Exception as e:
            self.text_log.append(f"[!] Не удалось запустить VLC: {e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())