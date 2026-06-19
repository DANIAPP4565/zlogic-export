"""
Z-Logic Exporter Assistant
Autor clínico: Dr. Olano Ricardo Daniel

App local para Windows que automatiza la exportación secuencial de estudios desde Z-Logic
mediante control de mouse/teclado. No modifica Z-Logic ni accede a bases internas: solo
reproduce los pasos que el usuario haría manualmente.

Instalación:
    py -m pip install pyautogui pyperclip
    py zlogic_exporter_app.py

Recomendado: ejecutar con Z-Logic en pantalla completa o siempre en la misma posición.
Movimiento de emergencia: llevar el mouse a la esquina superior izquierda para abortar.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import pyautogui
except Exception as exc:  # pragma: no cover
    pyautogui = None
    PYAUTOGUI_IMPORT_ERROR = exc
else:
    PYAUTOGUI_IMPORT_ERROR = None

try:
    import pyperclip
except Exception:  # pragma: no cover
    pyperclip = None

APP_NAME = "Z-Logic Exporter Assistant"
CONFIG_FILE = Path.home() / "zlogic_exporter_config.json"

COORD_KEYS = [
    ("open_studies", "Botón / menú: Abrir estudios"),
    ("last_study", "Último estudio de la lista"),
    ("export_button", "Botón / menú: Exportar"),
    ("save_accept", "Botón Aceptar/Guardar exportación"),
    ("next_study", "Botón / menú: Siguiente estudio"),
]


@dataclass
class ExporterConfig:
    zlogic_exe: str = ""
    export_dir: str = str(Path.home() / "Desktop" / "ZLogic_exportados")
    total_studies: int = 0  # 0 = continuar hasta que no aparezcan archivos nuevos varias veces
    start_with_last_study: bool = True
    click_open_studies: bool = True
    use_save_dialog: bool = False
    file_prefix: str = "zlogic_estudio"
    file_extension: str = ".txt"
    delay_between_actions: float = 0.8
    wait_after_launch: float = 5.0
    wait_after_export_click: float = 1.2
    max_wait_new_file: float = 20.0
    max_retries_without_new_file: int = 3
    coordinates: Dict[str, Optional[Tuple[int, int]]] = field(
        default_factory=lambda: {key: None for key, _ in COORD_KEYS}
    )

    def normalized_extension(self) -> str:
        ext = (self.file_extension or "").strip()
        if not ext:
            return ".txt"
        if not ext.startswith("."):
            ext = "." + ext
        return ext


def load_config() -> ExporterConfig:
    if not CONFIG_FILE.exists():
        return ExporterConfig()
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        cfg = ExporterConfig()
        for key, value in raw.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        # Convert coordinates from lists to tuples.
        fixed = {key: None for key, _ in COORD_KEYS}
        for key, value in (cfg.coordinates or {}).items():
            if value is None:
                fixed[key] = None
            elif isinstance(value, (list, tuple)) and len(value) == 2:
                fixed[key] = (int(value[0]), int(value[1]))
        cfg.coordinates = fixed
        return cfg
    except Exception:
        return ExporterConfig()


def save_config(cfg: ExporterConfig) -> None:
    CONFIG_FILE.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False), encoding="utf-8")


def safe_file_snapshot(folder: Path) -> Dict[Path, Tuple[int, float]]:
    """Returns file size and modified time without exposing patient names in the app log."""
    folder.mkdir(parents=True, exist_ok=True)
    snapshot: Dict[Path, Tuple[int, float]] = {}
    for item in folder.iterdir():
        if item.is_file():
            try:
                st = item.stat()
                snapshot[item] = (st.st_size, st.st_mtime)
            except OSError:
                continue
    return snapshot


def wait_for_new_or_changed_file(
    folder: Path,
    before: Dict[Path, Tuple[int, float]],
    timeout: float,
    stop_event: threading.Event,
) -> bool:
    """Waits until the export folder gets a new or modified stable file."""
    started = time.time()
    last_candidate: Optional[Path] = None
    last_size: Optional[int] = None
    stable_since: Optional[float] = None

    while time.time() - started < timeout:
        if stop_event.is_set():
            return False
        now = safe_file_snapshot(folder)
        candidates = []
        for path, sig in now.items():
            if path not in before or before.get(path) != sig:
                candidates.append(path)
        if candidates:
            candidate = max(candidates, key=lambda p: now[p][1])
            size = now[candidate][0]
            if candidate == last_candidate and size == last_size:
                if stable_since is None:
                    stable_since = time.time()
                if time.time() - stable_since >= 0.8:
                    return True
            else:
                last_candidate = candidate
                last_size = size
                stable_since = None
        time.sleep(0.25)
    return False


class ZLogicExporterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("980x720")
        self.minsize(880, 620)
        self.cfg = load_config()
        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.coord_vars: Dict[str, tk.StringVar] = {}
        self._build_ui()
        self._load_config_into_ui()
        self.after(150, self._drain_log_queue)

    def _build_ui(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.pack(fill="both", expand=True)

        title = ttk.Label(main, text=APP_NAME, font=("Segoe UI", 16, "bold"))
        title.pack(anchor="w")
        subtitle = ttk.Label(
            main,
            text="Automatización local y supervisada para exportar estudios Z-Logic en secuencia.",
        )
        subtitle.pack(anchor="w", pady=(0, 10))

        notebook = ttk.Notebook(main)
        notebook.pack(fill="both", expand=True)

        self.tab_general = ttk.Frame(notebook, padding=12)
        self.tab_coords = ttk.Frame(notebook, padding=12)
        self.tab_run = ttk.Frame(notebook, padding=12)
        notebook.add(self.tab_general, text="1. Configuración")
        notebook.add(self.tab_coords, text="2. Calibración de clics")
        notebook.add(self.tab_run, text="3. Exportación")

        self._build_general_tab()
        self._build_coords_tab()
        self._build_run_tab()

    def _build_general_tab(self) -> None:
        f = self.tab_general
        f.columnconfigure(1, weight=1)

        self.zlogic_exe_var = tk.StringVar()
        self.export_dir_var = tk.StringVar()
        self.total_studies_var = tk.IntVar()
        self.start_last_var = tk.BooleanVar()
        self.click_open_var = tk.BooleanVar()
        self.use_save_dialog_var = tk.BooleanVar()
        self.prefix_var = tk.StringVar()
        self.ext_var = tk.StringVar()
        self.delay_var = tk.DoubleVar()
        self.wait_launch_var = tk.DoubleVar()
        self.wait_export_var = tk.DoubleVar()
        self.max_wait_var = tk.DoubleVar()
        self.no_file_retries_var = tk.IntVar()

        row = 0
        ttk.Label(f, text="Ejecutable de Z-Logic").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(f, textvariable=self.zlogic_exe_var).grid(row=row, column=1, sticky="ew", padx=8)
        ttk.Button(f, text="Buscar...", command=self._select_zlogic_exe).grid(row=row, column=2, sticky="ew")

        row += 1
        ttk.Label(f, text="Carpeta de exportación").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(f, textvariable=self.export_dir_var).grid(row=row, column=1, sticky="ew", padx=8)
        ttk.Button(f, text="Elegir...", command=self._select_export_dir).grid(row=row, column=2, sticky="ew")

        row += 1
        ttk.Label(f, text="Cantidad de estudios").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Spinbox(f, from_=0, to=100000, textvariable=self.total_studies_var, width=12).grid(
            row=row, column=1, sticky="w", padx=8
        )
        ttk.Label(f, text="0 = continuar hasta detectar fin o falta repetida de archivos nuevos").grid(
            row=row, column=2, sticky="w"
        )

        row += 1
        ttk.Checkbutton(
            f,
            text="Al iniciar, abrir la pantalla/lista de estudios",
            variable=self.click_open_var,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=4)

        row += 1
        ttk.Checkbutton(
            f,
            text="Marcar automáticamente el último estudio antes de comenzar",
            variable=self.start_last_var,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=4)

        row += 1
        ttk.Checkbutton(
            f,
            text="La exportación abre ventana Guardar como / nombre de archivo",
            variable=self.use_save_dialog_var,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=4)

        row += 1
        ttk.Label(f, text="Prefijo si hay Guardar como").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(f, textvariable=self.prefix_var, width=24).grid(row=row, column=1, sticky="w", padx=8)
        ttk.Label(f, text="Extensión").grid(row=row, column=2, sticky="w")
        ttk.Entry(f, textvariable=self.ext_var, width=8).grid(row=row, column=2, sticky="e")

        sep = ttk.Separator(f)
        row += 1
        sep.grid(row=row, column=0, columnspan=3, sticky="ew", pady=14)

        row += 1
        ttk.Label(f, text="Pausa entre acciones, segundos").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Spinbox(f, from_=0.1, to=10.0, increment=0.1, textvariable=self.delay_var, width=12).grid(
            row=row, column=1, sticky="w", padx=8
        )

        row += 1
        ttk.Label(f, text="Espera tras abrir Z-Logic, segundos").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Spinbox(f, from_=1, to=60, increment=1, textvariable=self.wait_launch_var, width=12).grid(
            row=row, column=1, sticky="w", padx=8
        )

        row += 1
        ttk.Label(f, text="Espera tras clic Exportar, segundos").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Spinbox(f, from_=0.2, to=30, increment=0.2, textvariable=self.wait_export_var, width=12).grid(
            row=row, column=1, sticky="w", padx=8
        )

        row += 1
        ttk.Label(f, text="Tiempo máximo esperando archivo nuevo").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Spinbox(f, from_=2, to=120, increment=1, textvariable=self.max_wait_var, width=12).grid(
            row=row, column=1, sticky="w", padx=8
        )

        row += 1
        ttk.Label(f, text="Reintentos sin archivo nuevo antes de detener").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Spinbox(f, from_=1, to=20, increment=1, textvariable=self.no_file_retries_var, width=12).grid(
            row=row, column=1, sticky="w", padx=8
        )

        row += 1
        note = (
            "Uso recomendado: primero exportar 1 estudio manualmente para confirmar carpeta/formato. "
            "Luego calibrar los puntos de clic y hacer una prueba con 2 o 3 estudios."
        )
        ttk.Label(f, text=note, wraplength=850).grid(row=row, column=0, columnspan=3, sticky="w", pady=16)

        row += 1
        ttk.Button(f, text="Guardar configuración", command=self._save_from_ui).grid(row=row, column=0, sticky="w", pady=8)

    def _build_coords_tab(self) -> None:
        f = self.tab_coords
        f.columnconfigure(1, weight=1)
        instructions = (
            "Calibración: deje Z-Logic abierto en la pantalla correcta, ubique el cursor sobre cada botón o fila "
            "y presione 'Capturar posición actual'. La app guardará las coordenadas de pantalla."
        )
        ttk.Label(f, text=instructions, wraplength=850).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 12))

        for i, (key, label) in enumerate(COORD_KEYS, start=1):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=5)
            var = tk.StringVar(value="No calibrado")
            self.coord_vars[key] = var
            ttk.Entry(f, textvariable=var, state="readonly").grid(row=i, column=1, sticky="ew", padx=8)
            ttk.Button(f, text="Capturar posición actual", command=lambda k=key: self._capture_coord(k)).grid(
                row=i, column=2, sticky="ew", padx=4
            )
            ttk.Button(f, text="Probar clic", command=lambda k=key: self._test_click(k)).grid(
                row=i, column=3, sticky="ew", padx=4
            )

        ttk.Separator(f).grid(row=len(COORD_KEYS) + 1, column=0, columnspan=4, sticky="ew", pady=14)
        ttk.Label(
            f,
            text=(
                "Atajo de seguridad: pyautogui tiene FailSafe activado. Si algo se desvía, mueva el mouse "
                "rápidamente a la esquina superior izquierda de la pantalla. También puede usar el botón Detener."
            ),
            wraplength=850,
        ).grid(row=len(COORD_KEYS) + 2, column=0, columnspan=4, sticky="w")
        ttk.Button(f, text="Guardar coordenadas", command=self._save_from_ui).grid(
            row=len(COORD_KEYS) + 3, column=0, sticky="w", pady=12
        )

    def _build_run_tab(self) -> None:
        f = self.tab_run
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.start_btn = ttk.Button(top, text="Iniciar exportación", command=self._start_export)
        self.start_btn.pack(side="left", padx=(0, 8))
        self.stop_btn = ttk.Button(top, text="Detener", command=self._stop_export, state="disabled")
        self.stop_btn.pack(side="left")
        ttk.Button(top, text="Abrir carpeta exportada", command=self._open_export_dir).pack(side="left", padx=8)
        ttk.Button(top, text="Guardar configuración", command=self._save_from_ui).pack(side="left", padx=8)

        self.progress_var = tk.StringVar(value="Listo.")
        ttk.Label(f, textvariable=self.progress_var).pack(anchor="w", pady=(12, 4))

        self.log_text = tk.Text(f, height=28, wrap="word")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def _load_config_into_ui(self) -> None:
        cfg = self.cfg
        self.zlogic_exe_var.set(cfg.zlogic_exe)
        self.export_dir_var.set(cfg.export_dir)
        self.total_studies_var.set(cfg.total_studies)
        self.start_last_var.set(cfg.start_with_last_study)
        self.click_open_var.set(cfg.click_open_studies)
        self.use_save_dialog_var.set(cfg.use_save_dialog)
        self.prefix_var.set(cfg.file_prefix)
        self.ext_var.set(cfg.file_extension)
        self.delay_var.set(cfg.delay_between_actions)
        self.wait_launch_var.set(cfg.wait_after_launch)
        self.wait_export_var.set(cfg.wait_after_export_click)
        self.max_wait_var.set(cfg.max_wait_new_file)
        self.no_file_retries_var.set(cfg.max_retries_without_new_file)
        self._refresh_coord_vars()

    def _save_from_ui(self) -> bool:
        try:
            self.cfg.zlogic_exe = self.zlogic_exe_var.get().strip()
            self.cfg.export_dir = self.export_dir_var.get().strip()
            self.cfg.total_studies = int(self.total_studies_var.get())
            self.cfg.start_with_last_study = bool(self.start_last_var.get())
            self.cfg.click_open_studies = bool(self.click_open_var.get())
            self.cfg.use_save_dialog = bool(self.use_save_dialog_var.get())
            self.cfg.file_prefix = self.prefix_var.get().strip() or "zlogic_estudio"
            self.cfg.file_extension = self.ext_var.get().strip() or ".txt"
            self.cfg.delay_between_actions = float(self.delay_var.get())
            self.cfg.wait_after_launch = float(self.wait_launch_var.get())
            self.cfg.wait_after_export_click = float(self.wait_export_var.get())
            self.cfg.max_wait_new_file = float(self.max_wait_var.get())
            self.cfg.max_retries_without_new_file = int(self.no_file_retries_var.get())
            save_config(self.cfg)
            self.log(f"Configuración guardada en: {CONFIG_FILE}")
            return True
        except Exception as exc:
            messagebox.showerror("Error", f"No se pudo guardar la configuración:\n{exc}")
            return False

    def _select_zlogic_exe(self) -> None:
        path = filedialog.askopenfilename(
            title="Seleccionar ejecutable de Z-Logic",
            filetypes=[("Ejecutable", "*.exe"), ("Todos", "*.*")],
        )
        if path:
            self.zlogic_exe_var.set(path)

    def _select_export_dir(self) -> None:
        path = filedialog.askdirectory(title="Seleccionar carpeta de exportación")
        if path:
            self.export_dir_var.set(path)

    def _capture_coord(self, key: str) -> None:
        if pyautogui is None:
            messagebox.showerror("Falta dependencia", f"pyautogui no está disponible: {PYAUTOGUI_IMPORT_ERROR}")
            return
        x, y = pyautogui.position()
        self.cfg.coordinates[key] = (int(x), int(y))
        self._refresh_coord_vars()
        self._save_from_ui()
        self.log(f"Coordenada capturada para '{key}': X={x}, Y={y}")

    def _test_click(self, key: str) -> None:
        if pyautogui is None:
            messagebox.showerror("Falta dependencia", f"pyautogui no está disponible: {PYAUTOGUI_IMPORT_ERROR}")
            return
        coord = self.cfg.coordinates.get(key)
        if not coord:
            messagebox.showwarning("Sin calibrar", "Primero capture esta coordenada.")
            return
        self.log(f"Prueba de clic en '{key}'.")
        pyautogui.click(coord[0], coord[1])

    def _refresh_coord_vars(self) -> None:
        for key, _label in COORD_KEYS:
            coord = self.cfg.coordinates.get(key)
            text = "No calibrado" if not coord else f"X={coord[0]}, Y={coord[1]}"
            if key in self.coord_vars:
                self.coord_vars[key].set(text)

    def _start_export(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("En ejecución", "La exportación ya está en curso.")
            return
        if not self._save_from_ui():
            return
        if pyautogui is None:
            messagebox.showerror(
                "Falta dependencia",
                "Debe instalar pyautogui:\n\npy -m pip install pyautogui pyperclip",
            )
            return
        missing = self._required_missing_coords()
        if missing:
            messagebox.showwarning(
                "Falta calibración",
                "Faltan coordenadas requeridas:\n" + "\n".join(f"- {m}" for m in missing),
            )
            return
        self.stop_event.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress_var.set("Exportación en curso...")
        self.worker = threading.Thread(target=self._run_export_worker, daemon=True)
        self.worker.start()

    def _stop_export(self) -> None:
        self.stop_event.set()
        self.log("Solicitud de detención recibida. La app se detendrá al terminar la acción actual.")

    def _required_missing_coords(self) -> list[str]:
        missing = []
        if self.cfg.click_open_studies and not self.cfg.coordinates.get("open_studies"):
            missing.append("Botón / menú: Abrir estudios")
        if self.cfg.start_with_last_study and not self.cfg.coordinates.get("last_study"):
            missing.append("Último estudio de la lista")
        if not self.cfg.coordinates.get("export_button"):
            missing.append("Botón / menú: Exportar")
        if not self.cfg.use_save_dialog and not self.cfg.coordinates.get("save_accept"):
            # Some Z-Logic versions export directly. To avoid over-restricting, this is optional if the user sets save dialog.
            pass
        if not self.cfg.coordinates.get("next_study"):
            missing.append("Botón / menú: Siguiente estudio")
        return missing

    def _run_export_worker(self) -> None:
        try:
            self._export_sequence()
        except pyautogui.FailSafeException:
            self.log("ABORTADO: FailSafe activado por esquina superior izquierda del mouse.")
        except Exception as exc:
            self.log(f"ERROR: {exc}")
        finally:
            self.stop_event.set()
            self.after(0, self._worker_finished)

    def _worker_finished(self) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.progress_var.set("Finalizado o detenido. Revise el log y la carpeta de exportación.")

    def _export_sequence(self) -> None:
        pyautogui.PAUSE = max(0.05, float(self.cfg.delay_between_actions))
        pyautogui.FAILSAFE = True
        export_dir = Path(self.cfg.export_dir)
        export_dir.mkdir(parents=True, exist_ok=True)

        self.log("Iniciando flujo de exportación supervisada.")
        self.log("No se registran nombres de pacientes en el log; solo conteos y estado.")

        if self.cfg.zlogic_exe:
            exe = Path(self.cfg.zlogic_exe)
            if exe.exists():
                self.log("Abriendo Z-Logic...")
                subprocess.Popen([str(exe)], cwd=str(exe.parent))
                self._sleep_interruptible(self.cfg.wait_after_launch)
            else:
                self.log("El ejecutable configurado no existe. Continúo asumiendo que Z-Logic ya está abierto.")
        else:
            self.log("No se configuró ejecutable. Continúo asumiendo que Z-Logic ya está abierto.")

        if self.cfg.click_open_studies:
            self._click("open_studies", "Abriendo lista de estudios")

        if self.cfg.start_with_last_study:
            self._click("last_study", "Seleccionando último estudio")

        total_target = int(self.cfg.total_studies)
        exported = 0
        no_file_rounds = 0
        max_no_file = max(1, int(self.cfg.max_retries_without_new_file))

        while not self.stop_event.is_set():
            if total_target > 0 and exported >= total_target:
                self.log(f"Objetivo alcanzado: {exported} estudios procesados.")
                break

            before = safe_file_snapshot(export_dir)
            current_number = exported + 1
            self.log(f"Procesando estudio #{current_number}...")

            self._click("export_button", "Clic en Exportar")
            self._sleep_interruptible(self.cfg.wait_after_export_click)

            if self.cfg.use_save_dialog:
                self._handle_save_dialog(current_number)
            else:
                coord = self.cfg.coordinates.get("save_accept")
                if coord:
                    self._click("save_accept", "Confirmando exportación")
                else:
                    self.log("Sin clic de confirmación: se asume exportación directa.")

            changed = wait_for_new_or_changed_file(
                export_dir,
                before,
                timeout=float(self.cfg.max_wait_new_file),
                stop_event=self.stop_event,
            )
            if changed:
                exported += 1
                no_file_rounds = 0
                self.log(f"Archivo nuevo/modificado detectado. Estudios exportados: {exported}.")
            else:
                no_file_rounds += 1
                self.log(
                    f"No se detectó archivo nuevo en la carpeta. Intento sin archivo: {no_file_rounds}/{max_no_file}."
                )
                if no_file_rounds >= max_no_file:
                    self.log("Se detiene por falta repetida de archivos nuevos. Puede indicar fin de lista o diálogo no calibrado.")
                    break

            if total_target > 0 and exported >= total_target:
                self.log(f"Objetivo alcanzado: {exported} estudios exportados.")
                break

            self._click("next_study", "Pasando al siguiente estudio")
            self._sleep_interruptible(self.cfg.delay_between_actions)

        self.log(f"Resumen: {exported} estudios con archivo detectado en carpeta de exportación.")

    def _handle_save_dialog(self, index: int) -> None:
        export_dir = Path(self.cfg.export_dir)
        ext = self.cfg.normalized_extension()
        filename = f"{self.cfg.file_prefix}_{index:05d}{ext}"
        full_path = str(export_dir / filename)
        self.log("Completando ventana Guardar como con nombre anonimizado/secuencial.")
        if pyperclip is not None:
            pyperclip.copy(full_path)
            pyautogui.hotkey("ctrl", "v")
        else:
            pyautogui.write(full_path, interval=0.001)
        pyautogui.press("enter")
        # If Windows asks overwrite/confirm, pressing enter again is usually safe when files are unique.
        self._sleep_interruptible(0.4)

    def _click(self, key: str, label: str) -> None:
        coord = self.cfg.coordinates.get(key)
        if not coord:
            raise RuntimeError(f"Falta coordenada para: {key}")
        if self.stop_event.is_set():
            return
        self.log(label + ".")
        pyautogui.click(coord[0], coord[1])

    def _sleep_interruptible(self, seconds: float) -> None:
        end = time.time() + max(0.0, float(seconds))
        while time.time() < end:
            if self.stop_event.is_set():
                break
            time.sleep(0.1)

    def _open_export_dir(self) -> None:
        folder = Path(self.export_dir_var.get().strip() or self.cfg.export_dir)
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{stamp}] {message}")

    def _drain_log_queue(self) -> None:
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(150, self._drain_log_queue)


def main() -> None:
    app = ZLogicExporterApp()
    app.mainloop()


if __name__ == "__main__":
    main()
