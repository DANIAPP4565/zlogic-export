# Z-Logic Exporter Assistant

App local para Windows que automatiza la exportación secuencial de estudios desde Z-Logic con mouse/teclado.

## Instalación

```bash
py -m pip install -r requirements_zlogic_exporter.txt
py zlogic_exporter_app.py
```

## Flujo recomendado

1. Abrir la app.
2. Configurar ruta del ejecutable de Z-Logic y carpeta de exportación.
3. Abrir Z-Logic y dejarlo en la pantalla usual.
4. Calibrar coordenadas: Abrir estudios, último estudio, Exportar, Guardar/Aceptar y Siguiente.
5. Hacer una prueba con 2 o 3 estudios.
6. Ejecutar el lote completo.

## Seguridad

- La app no modifica Z-Logic ni su base interna.
- Solo reproduce acciones del usuario.
- El log no guarda nombres de pacientes.
- Para abortar: mover el mouse a la esquina superior izquierda o presionar Detener.
