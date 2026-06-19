"""
Z-Logic Exporter Assistant - Streamlit local/Windows
Autor clínico: Dr. Olano Ricardo Daniel

IMPORTANTE
---------
Esta app debe ejecutarse en la MISMA PC Windows donde está instalado Z-Logic.
Streamlit Cloud no puede controlar Z-Logic porque corre en un servidor Linux sin escritorio,
sin el programa instalado y sin acceso al mouse/teclado del usuario.

Ejecución local recomendada:
    py -m pip install streamlit pyautogui pyperclip
    py -m streamlit run zlogic_exporter_streamlit_app.py

Seguridad:
- La automatización reproduce clics/teclas del usuario. No modifica Z-Logic ni accede a su base interna.
- Para abortar: mover el mouse a la esquina superior izquierda de la pantalla.
- Probar primero con 1-3 estudios antes de ejecutar el lote completo.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

import streamlit as st

APP_NAME = "Z-Logic Exporter Assistant"
CONFIG_FILE = Path.home() / "zlogic_exporter_config_streamlit.json"

COORD_KEYS = [
    ("open_studies", "Botón / menú: Abrir estudios"),
    ("last_study", "Último estudio de la lista"),
    ("export_button", "Botón / menú: Exportar"),
    ("save_accept", "Botón Aceptar/Guardar exportación"),
    ("next_study", "Botón / fila: Siguiente estudio"),
]


@dataclass
class ExporterConfig:
    zlogic_exe: str = ""
    export_dir: str = str(Path.home() / "Desktop" / "ZLogic_exportados")
    total_studies: int = 1
    start_with_last_study: bool = True
    click_open_studies: bool = True
    use_save_dialog: bool = False
    file_prefix: str = "zlogic_estudio"
    file_extension: str = ".txt"
    delay_between_actions: float = 0.8
    wait_after_launch: float = 5.0
    wait_after_export_click: float = 1.5
    max_wait_new_file: float = 20.0
    max_retries_without_new_file: int = 3
    coordinates: Dict[str, Optional[Tuple[int, int]]] = field(
        default_factory=lambda: {key: None for key, _ in COORD_KEYS}
    )

    def normalized_extension(self) -> str:
        ext = (self.file_extension or "").strip()
        if not ext:
            return ".txt"
        return ext if ext.startswith(".") else f".{ext}"


def load_config() -> ExporterConfig:
    cfg = ExporterConfig()
    if not CONFIG_FILE.exists():
        return cfg
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        for key, value in raw.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        fixed = {key: None for key, _ in COORD_KEYS}
        for key, value in (cfg.coordinates or {}).items():
            if value is None:
                fixed[key] = None
            elif isinstance(value, (list, tuple)) and len(value) == 2:
                fixed[key] = (int(value[0]), int(value[1]))
        cfg.coordinates = fixed
    except Exception:
        cfg = ExporterConfig()
    return cfg


def save_config(cfg: ExporterConfig) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        st.warning(f"No se pudo guardar la configuración local: {exc}")


def ensure_cfg() -> ExporterConfig:
    if "cfg" not in st.session_state:
        st.session_state.cfg = load_config()
    return st.session_state.cfg


def get_pyautogui():
    """Import pyautogui only when needed, so the app does not crash on Streamlit Cloud."""
    try:
        import pyautogui  # type: ignore

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
        return pyautogui, None
    except Exception as exc:  # noqa: BLE001
        return None, exc


def get_pyperclip():
    try:
        import pyperclip  # type: ignore

        return pyperclip
    except Exception:
        return None


def is_probably_cloud() -> bool:
    text = " ".join([
        os.environ.get("STREAMLIT_SERVER_HEADLESS", ""),
        os.environ.get("HOSTNAME", ""),
        os.environ.get("HOME", ""),
        os.getcwd(),
    ]).lower()
    return "mount/src" in text or "streamlit" in text and platform.system().lower() == "linux"


def local_desktop_status() -> Tuple[bool, str]:
    if platform.system().lower() != "windows":
        return False, "La automatización de Z-Logic requiere ejecutarse localmente en Windows."
    pyautogui, exc = get_pyautogui()
    if pyautogui is None:
        return False, f"Falta instalar pyautogui o no hay escritorio disponible. Detalle: {exc}"
    return True, "Entorno local compatible detectado."


def file_snapshot(folder: Path) -> Dict[Path, Tuple[int, float]]:
    folder.mkdir(parents=True, exist_ok=True)
    snap: Dict[Path, Tuple[int, float]] = {}
    for item in folder.iterdir():
        if item.is_file():
            try:
                stat = item.stat()
                snap[item] = (stat.st_size, stat.st_mtime)
            except OSError:
                pass
    return snap


def wait_for_new_or_changed_file(
    folder: Path,
    before: Dict[Path, Tuple[int, float]],
    timeout: float,
    log_area,
    log_lines: list[str],
) -> bool:
    started = time.time()
    last_candidate: Optional[Path] = None
    last_size: Optional[int] = None
    stable_since: Optional[float] = None

    while time.time() - started < timeout:
        now = file_snapshot(folder)
        candidates: list[Path] = []
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

        elapsed = int(time.time() - started)
        if elapsed > 0 and elapsed % 5 == 0:
            write_log(log_area, log_lines, f"Esperando archivo nuevo/modificado... {elapsed}s")
            time.sleep(0.35)
        else:
            time.sleep(0.25)
    return False


def write_log(log_area, log_lines: list[str], message: str) -> None:
    timestamp = time.strftime("%H:%M:%S")
    log_lines.append(f"[{timestamp}] {message}")
    log_area.code("\n".join(log_lines[-250:]), language="text")


def click_coord(pyautogui, cfg: ExporterConfig, key: str, label: str, log_area, log_lines: list[str]) -> None:
    coord = cfg.coordinates.get(key)
    if not coord:
        raise RuntimeError(f"Falta calibrar: {label}")
    x, y = coord
    write_log(log_area, log_lines, f"Clic: {label} ({x}, {y})")
    pyautogui.click(x, y)
    time.sleep(cfg.delay_between_actions)


def type_or_paste_path(pyautogui, full_path: str) -> None:
    pyperclip = get_pyperclip()
    if pyperclip is not None:
        pyperclip.copy(full_path)
        pyautogui.hotkey("ctrl", "v")
    else:
        pyautogui.write(full_path, interval=0.002)


def validate_for_run(cfg: ExporterConfig) -> list[str]:
    errors: list[str] = []
    if cfg.zlogic_exe and not Path(cfg.zlogic_exe).exists():
        errors.append("La ruta del ejecutable de Z-Logic no existe. Puede dejarla vacía si Z-Logic ya está abierto.")
    if cfg.total_studies < 0:
        errors.append("La cantidad de estudios no puede ser negativa.")
    needed = []
    if cfg.click_open_studies:
        needed.append(("open_studies", "Abrir estudios"))
    if cfg.start_with_last_study:
        needed.append(("last_study", "Último estudio"))
    needed.append(("export_button", "Exportar"))
    if cfg.use_save_dialog:
        needed.append(("save_accept", "Aceptar/Guardar"))
    needed.append(("next_study", "Siguiente estudio"))
    for key, label in needed:
        if not cfg.coordinates.get(key):
            errors.append(f"Falta calibrar coordenada: {label}.")
    return errors


def run_export(cfg: ExporterConfig, studies_to_run: int, dry_run: bool = False) -> None:
    compatible, msg = local_desktop_status()
    if not compatible:
        st.error(msg)
        return

    validation = validate_for_run(cfg)
    if validation:
        st.error("Hay datos incompletos para iniciar la automatización.")
        for item in validation:
            st.write(f"- {item}")
        return

    pyautogui, exc = get_pyautogui()
    if pyautogui is None:
        st.error(f"No se pudo iniciar pyautogui: {exc}")
        return

    export_folder = Path(cfg.export_dir)
    export_folder.mkdir(parents=True, exist_ok=True)

    log_lines: list[str] = []
    log_area = st.empty()
    progress = st.progress(0)

    write_log(log_area, log_lines, "Inicio de automatización supervisada.")
    write_log(log_area, log_lines, "Para abortar: mover el mouse a la esquina superior izquierda.")

    try:
        if cfg.zlogic_exe:
            write_log(log_area, log_lines, "Abriendo Z-Logic...")
            subprocess.Popen([cfg.zlogic_exe], shell=False)
            time.sleep(cfg.wait_after_launch)
        else:
            write_log(log_area, log_lines, "No se indicó ejecutable: se asume Z-Logic ya abierto.")
            time.sleep(1)

        if cfg.click_open_studies:
            click_coord(pyautogui, cfg, "open_studies", "Abrir estudios", log_area, log_lines)

        if cfg.start_with_last_study:
            click_coord(pyautogui, cfg, "last_study", "Seleccionar último estudio", log_area, log_lines)

        if dry_run:
            write_log(log_area, log_lines, "Modo prueba: se ejecutará un solo estudio.")
            studies_to_run = 1

        if studies_to_run == 0:
            write_log(log_area, log_lines, "Modo automático sin cantidad fija: se detendrá ante falta repetida de archivos nuevos.")
            total_for_progress = cfg.max_retries_without_new_file + 1
        else:
            total_for_progress = studies_to_run

        exported = 0
        failed_consecutive = 0
        index = 1

        while True:
            if studies_to_run and index > studies_to_run:
                break
            if not studies_to_run and failed_consecutive >= cfg.max_retries_without_new_file:
                write_log(log_area, log_lines, "Fin probable: no se detectaron archivos nuevos en intentos consecutivos.")
                break

            write_log(log_area, log_lines, f"Procesando estudio #{index}.")
            before = file_snapshot(export_folder)

            click_coord(pyautogui, cfg, "export_button", "Exportar", log_area, log_lines)
            time.sleep(cfg.wait_after_export_click)

            if cfg.use_save_dialog:
                filename = f"{cfg.file_prefix}_{index:05d}{cfg.normalized_extension()}"
                full_path = str(export_folder / filename)
                write_log(log_area, log_lines, "Completando nombre de archivo anónimo para guardar.")
                type_or_paste_path(pyautogui, full_path)
                time.sleep(cfg.delay_between_actions)
                click_coord(pyautogui, cfg, "save_accept", "Aceptar/Guardar", log_area, log_lines)

            ok = wait_for_new_or_changed_file(
                folder=export_folder,
                before=before,
                timeout=cfg.max_wait_new_file,
                log_area=log_area,
                log_lines=log_lines,
            )

            if ok:
                exported += 1
                failed_consecutive = 0
                write_log(log_area, log_lines, "Exportación detectada correctamente.")
            else:
                failed_consecutive += 1
                write_log(
                    log_area,
                    log_lines,
                    f"No se detectó archivo nuevo/modificado. Intento fallido consecutivo {failed_consecutive}.",
                )

            progress_value = min(1.0, index / max(1, total_for_progress)) if studies_to_run else min(
                1.0, failed_consecutive / max(1, cfg.max_retries_without_new_file)
            )
            progress.progress(progress_value)

            # Avanzar al siguiente estudio, incluso si falló, salvo que sea el último definido.
            if not studies_to_run or index < studies_to_run:
                click_coord(pyautogui, cfg, "next_study", "Siguiente estudio", log_area, log_lines)

            index += 1

        write_log(log_area, log_lines, f"Proceso finalizado. Estudios exportados detectados: {exported}.")
        st.success(f"Proceso finalizado. Exportaciones detectadas: {exported}.")

    except pyautogui.FailSafeException:
        write_log(log_area, log_lines, "Automatización abortada por FAILSAFE: mouse en esquina superior izquierda.")
        st.warning("Automatización detenida por seguridad.")
    except Exception as exc:  # noqa: BLE001
        write_log(log_area, log_lines, f"ERROR: {exc}")
        st.error(f"Se detuvo la automatización: {exc}")


def render_header() -> None:
    st.set_page_config(page_title=APP_NAME, layout="wide")
    st.title("Exportador automatizado para Z-Logic")
    st.caption("App local supervisada para exportar estudios uno por uno desde Z-Logic.")

    if is_probably_cloud() or platform.system().lower() != "windows":
        st.warning(
            "Esta pantalla puede abrir en Streamlit Cloud, pero la automatización real solo funciona "
            "ejecutando la app localmente en la PC Windows donde está instalado Z-Logic. "
            "Cloud no tiene acceso al programa, al mouse ni al escritorio del usuario."
        )


def render_config_tab(cfg: ExporterConfig) -> None:
    st.subheader("1. Configuración general")
    col1, col2 = st.columns([2, 1])
    with col1:
        cfg.zlogic_exe = st.text_input(
            "Ruta del ejecutable de Z-Logic",
            cfg.zlogic_exe,
            help="Ejemplo: C:\\Program Files\\ZLogic\\ZLogic.exe. Puede quedar vacío si Z-Logic ya está abierto.",
        )
        cfg.export_dir = st.text_input("Carpeta de exportación", cfg.export_dir)
        cfg.total_studies = st.number_input(
            "Cantidad de estudios a exportar",
            min_value=0,
            max_value=100000,
            value=int(cfg.total_studies),
            step=1,
            help="0 = continuar hasta que no se detecten archivos nuevos en varios intentos.",
        )
        cfg.file_prefix = st.text_input("Prefijo anónimo para archivos", cfg.file_prefix)
        cfg.file_extension = st.text_input("Extensión esperada", cfg.file_extension)
    with col2:
        cfg.start_with_last_study = st.checkbox("Iniciar marcando el último estudio", value=cfg.start_with_last_study)
        cfg.click_open_studies = st.checkbox("Hacer clic en Abrir estudios al inicio", value=cfg.click_open_studies)
        cfg.use_save_dialog = st.checkbox(
            "Z-Logic abre diálogo Guardar como",
            value=cfg.use_save_dialog,
            help="Activar si al exportar se abre una ventana para escribir nombre y confirmar guardado.",
        )
        cfg.delay_between_actions = st.number_input("Pausa entre acciones, segundos", 0.1, 10.0, float(cfg.delay_between_actions), 0.1)
        cfg.wait_after_launch = st.number_input("Espera tras abrir Z-Logic, segundos", 0.0, 60.0, float(cfg.wait_after_launch), 0.5)
        cfg.wait_after_export_click = st.number_input("Espera tras clic Exportar, segundos", 0.0, 60.0, float(cfg.wait_after_export_click), 0.5)
        cfg.max_wait_new_file = st.number_input("Espera máxima de archivo nuevo, segundos", 2.0, 180.0, float(cfg.max_wait_new_file), 1.0)
        cfg.max_retries_without_new_file = st.number_input("Cortes por fallas consecutivas", 1, 20, int(cfg.max_retries_without_new_file), 1)

    if st.button("Guardar configuración", type="primary"):
        save_config(cfg)
        st.success("Configuración guardada en esta PC.")

    st.info(
        "Recomendación: usar siempre la misma resolución de pantalla, Z-Logic maximizado y una carpeta de exportación vacía para la primera prueba."
    )


def render_calibration_tab(cfg: ExporterConfig) -> None:
    st.subheader("2. Calibración de coordenadas")
    st.write(
        "Para cada punto, pulse el botón de captura y tendrá 5 segundos para colocar el mouse sobre el botón o fila correspondiente en Z-Logic."
    )

    compatible, msg = local_desktop_status()
    if compatible:
        st.success(msg)
    else:
        st.warning(msg)

    delay = st.slider("Segundos para mover el mouse antes de capturar", 2, 10, 5)

    for key, label in COORD_KEYS:
        col1, col2, col3 = st.columns([2, 1, 1])
        current = cfg.coordinates.get(key)
        with col1:
            st.write(f"**{label}**")
            st.caption(f"Actual: {current if current else 'sin calibrar'}")
        with col2:
            if st.button(f"Capturar", key=f"cap_{key}"):
                pyautogui, exc = get_pyautogui()
                if pyautogui is None:
                    st.error(f"No se pudo usar pyautogui: {exc}")
                else:
                    placeholder = st.empty()
                    for remaining in range(delay, 0, -1):
                        placeholder.warning(f"Mueva el mouse a: {label}. Captura en {remaining}...")
                        time.sleep(1)
                    pos = pyautogui.position()
                    cfg.coordinates[key] = (int(pos.x), int(pos.y))
                    save_config(cfg)
                    placeholder.success(f"Coordenada capturada para {label}: ({pos.x}, {pos.y})")
        with col3:
            if st.button("Borrar", key=f"del_{key}"):
                cfg.coordinates[key] = None
                save_config(cfg)
                st.rerun()

    st.divider()
    if st.button("Guardar coordenadas"):
        save_config(cfg)
        st.success("Coordenadas guardadas.")


def render_run_tab(cfg: ExporterConfig) -> None:
    st.subheader("3. Ejecutar exportación")

    validation = validate_for_run(cfg)
    if validation:
        st.warning("Antes de ejecutar faltan estos puntos:")
        for item in validation:
            st.write(f"- {item}")
    else:
        st.success("Configuración mínima completa.")

    st.write("**Secuencia que ejecutará:**")
    steps = []
    if cfg.zlogic_exe:
        steps.append("Abrir Z-Logic")
    else:
        steps.append("Usar Z-Logic ya abierto")
    if cfg.click_open_studies:
        steps.append("Abrir estudios")
    if cfg.start_with_last_study:
        steps.append("Marcar último estudio")
    steps.extend(["Exportar", "Guardar/confirmar si corresponde", "Detectar archivo nuevo", "Ir al siguiente estudio", "Repetir"])
    st.write(" → ".join(steps))

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Probar con 1 estudio", type="secondary"):
            save_config(cfg)
            run_export(cfg, studies_to_run=1, dry_run=True)
    with col2:
        if st.button("Iniciar exportación completa", type="primary"):
            save_config(cfg)
            run_export(cfg, studies_to_run=int(cfg.total_studies), dry_run=False)

    st.error(
        "No use la PC durante la automatización. Para abortar, lleve el mouse a la esquina superior izquierda."
    )


def render_instructions_tab() -> None:
    st.subheader("Instalación local en Windows")
    st.code(
        "py -m pip install streamlit pyautogui pyperclip\n"
        "py -m streamlit run zlogic_exporter_streamlit_app.py",
        language="powershell",
    )
    st.write(
        "Streamlit Cloud sirve para mostrar la interfaz, pero no puede exportar desde Z-Logic. "
        "Para automatizar realmente debe ejecutarse localmente, porque el robot necesita controlar mouse, teclado y ventanas del programa."
    )
    st.subheader("Flujo recomendado")
    st.markdown(
        """
1. Abrir Z-Logic y dejarlo maximizado.  
2. Abrir esta app local en el navegador.  
3. Configurar carpeta de exportación.  
4. Calibrar los puntos de clic.  
5. Probar con 1 estudio.  
6. Probar con 3 estudios.  
7. Ejecutar el lote completo.
        """.strip()
    )


def main() -> None:
    cfg = ensure_cfg()
    render_header()

    tab1, tab2, tab3, tab4 = st.tabs([
        "Configuración",
        "Calibración",
        "Exportación",
        "Instalación",
    ])
    with tab1:
        render_config_tab(cfg)
    with tab2:
        render_calibration_tab(cfg)
    with tab3:
        render_run_tab(cfg)
    with tab4:
        render_instructions_tab()


if __name__ == "__main__":
    main()
