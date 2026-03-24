# Auditoría: Supuestos de Colombia / Departamentos en el Código

> **Propósito:** Inventario de todos los valores y lógicas que están codificados directamente para el mercado colombiano. Este documento **no propone cambios**; su objetivo es informar al cliente sobre qué partes del sistema necesitarán revisión si el producto se expande a otros mercados.
>
> **Última actualización:** 2026-03-22

---

## 1. Zona horaria — fija en `America/Bogota`

Toda operación de fecha y hora en el sistema convierte o localiza en la Hora Estándar de Colombia (UTC-5). No existe ningún mecanismo para seleccionar una zona horaria diferente en tiempo de ejecución.

| Archivo | Línea(s) | Detalle |
|---------|----------|---------|
| `src/utils/datetime_utils.py` | 9–10 | `COLOMBIA_TIMEZONE_NAME = "America/Bogota"` — constante central; todas las funciones auxiliares (`now_colombia`, `utc_to_colombia_*`, `normalize_datetime_columns_to_colombia`) se apoyan en esta constante |
| `src/config.py` | 90 | La variable de entorno `ARTIFACT_TIMEZONE` tiene como valor por defecto `"America/Bogota"` |
| `src/io/artifact_naming.py` | 16 | Retrocede a `"America/Bogota"` si la variable de entorno no está definida |
| `src/availability/__init__.py` | 19 | `ZoneInfo("America/Bogota")` escrito directamente en el código |
| `src/availability/slot_scanner.py` | 29 | `COLOMBIA_TZ = "America/Bogota"` |
| `src/availability/schedule_loader.py` | 29 | `COLOMBIA_TZ = "America/Bogota"` |
| `src/availability/feasibility_probe.py` | 31 | `COLOMBIA_TZ = "America/Bogota"` |
| `src/availability/service_builder.py` | 17 | `COLOMBIA_TZ = "America/Bogota"` |
| `src/optimization/common/preassigned.py` | 13, 40–65, 459–464, 518 | `_BOGOTA_TZ_DTYPE = "datetime64[ns, America/Bogota]"` — usado como tipo de dato de pandas en todas las columnas de timestamp |
| `src/optimization/algorithms/react/algorithm.py` | 107 | `decision_time.tz_localize("America/Bogota")` escrito directamente |
| `src/optimization/algorithms/offline/offline_algorithms.py` | 111 | `tzinfo="America/Bogota"` escrito directamente |
| `src/analysis/solution_evaluation.py` | 52, 102–108 | `BOGOTA_TZ = ZoneInfo("America/Bogota")` — todos los timestamps de salida se formatean en esta zona |

---

## 2. Códigos de departamento — solo códigos DANE de Colombia

El solver usa los códigos numéricos del DANE (Departamento Administrativo Nacional de Estadística) como clave geográfica principal en todo el pipeline. Las tablas de datos maestros, la lógica de filtrado y el ajuste de hiperparámetros por iteraciones están todos indexados por estos códigos.

### 2a. Número de iteraciones del optimizador por departamento

Configurado directamente en `src/optimization/settings/solver_settings.py` (líneas 21–51):

| Código | Departamento | Ciudad principal | OFFLINE | INSERT | REACT |
|--------|-------------|-----------------|---------|--------|-------------|
| `"25"` | Cundinamarca | Bogotá | 5 000 | 1 000 | 1 000 |
| `"5"` | Antioquia | Medellín | 2 000 | 2 000 | 2 000 |
| `"76"` | Valle del Cauca | Cali | 2 000 | 2 000 | 2 000 |
| `"8"` | Atlántico | Barranquilla | 500 | 500 | 500 |
| `"13"` | Bolívar | Cartagena | 500 | 500 | 500 |
| `"68"` | Santander | Bucaramanga | 500 | 500 | 500 |
| `"66"` | Risaralda | Pereira | 500 | 500 | 500 |

Cualquier código de departamento que no aparezca en esta tabla recibe cero iteraciones (funcionalmente no soportado).

### 2b. Regla de validación

`src/data/validation/rules/domain.py` — la clase `ValidDepartmentsOnly` valida que los registros entrantes usen solo los códigos permitidos. Los registros con códigos desconocidos son rechazados o marcados como inválidos.

### 2c. Los datos maestros están indexados por código de departamento

Las tres tablas de datos maestros usan el código de departamento colombiano como índice:

| Archivo | Propósito |
|---------|-----------|
| `data/master_data/directorio.csv` | Directorio canónico de ciudades y ubicaciones |
| `data/master_data/duraciones.csv` | Matriz de tiempos de viaje por departamento |
| `data/master_data/dist_dict.pkl` | Diccionario de distancias precalculadas |
| `data/master_data/pico_y_placa.json` | Reglas de pico y placa por departamento |

Cargador: `src/data/loading/master_data_loader.py`

---

## 3. Pico y Placa — restricción vehicular exclusiva de Colombia

Existe un subsistema completo dedicado al *Pico y Placa*, la restricción de circulación vehicular vigente en varias ciudades colombianas. Bloquea los vehículos en función del último dígito de la placa según el día.

| Archivo | Detalle |
|---------|---------|
| `src/availability/pico_placa.py` | Módulo completo: carga la configuración, extrae el dígito de la placa y evalúa la restricción |
| `data/master_data/pico_y_placa.json` | Reglas para los 7 departamentos anteriores; dos tipos de lógica: `parity` (día par/impar del mes) y `weekday` (rotación por día de la semana) |
| `src/availability/__init__.py` | 80–84 | La verificación de pico y placa se ejecuta durante el chequeo de disponibilidad; una placa restringida genera el evento `pico_y_placa_restricted` y bloquea el slot |

El módulo es *fail-open*: si un departamento no está en la configuración, la restricción se omite. No existe un concepto equivalente en ningún otro mercado.

---

## 4. Parámetros de tiempos operacionales en español

`src/optimization/settings/model_params.py` (líneas 27–33) — todas las constantes de tiempo usan nombres en español y están calibradas para las operaciones en Colombia:

| Parámetro | Valor por defecto | Significado |
|-----------|------------------|-------------|
| `tiempo_previo_min` | `0` min | Tiempo de anticipación antes de la fecha programada |
| `tiempo_gracia_min` | `15` min | Tiempo de gracia |
| `tiempo_alistar_min` | `30` min | Tiempo de preparación del vehículo |
| `tiempo_other_min` | `30` min | Buffer misceláneo |
| `tiempo_finalizacion_min` | `15` min | Tiempo de cierre del servicio |
| `workday_end_str` | `"19:00:00"` | Límite de fin de jornada laboral (7 PM) |

---

## 5. Velocidades de viaje por defecto — calibradas para ciudades colombianas

`src/optimization/settings/model_params.py` (líneas 23–24):

| Parámetro | Valor | Uso |
|-----------|-------|-----|
| `alfred_speed_kmh` | `20.0 km/h` | Conductor desplazándose al primer punto de servicio |
| `vehicle_transport_speed_kmh` | `32 km/h` | Transporte del vehículo entre ubicaciones |

Estos valores fueron calibrados con base en el tráfico de Bogotá. Se propagan a través del método de distancia/tiempo basado en velocidad en todos los algoritmos.

---

## 6. Funciones auxiliares de datetime nombradas por Colombia

`src/utils/datetime_utils.py` expone las siguientes funciones, todas atadas a la zona horaria colombiana:

- `now_colombia()` — retorna la hora actual en la zona de Bogotá
- `utc_to_colombia_series()` / `utc_to_colombia_timestamp()` — convierten UTC a zona horaria de Colombia
- `normalize_datetime_columns_to_colombia()` — normaliza columnas de un DataFrame en lote

Estas funciones se invocan en tiempo de parseo para servicios y directorio de conductores:

| Archivo | Línea(s) | Llamada |
|---------|----------|---------|
| `src/data/parsing/input_parser.py` | 7, 93 | `normalize_datetime_columns_to_colombia` |
| `src/data/parsing/driver_directory_parser.py` | 10, 129, 136 | `utc_to_colombia_timestamp` |
| `src/availability/request_parser.py` | 46, 90 | `now_colombia()` como `as_of_time` por defecto |

---

## 7. Mínimo de 2 horas de anticipación para agendar servicios

`src/data/validation/rules/domain.py` — la regla `CreatedBeforeScheduleRule` exige que un servicio se cree al menos 2 horas antes de su fecha programada. Esta es una convención operacional colombiana y no está validada contra ningún estándar externo.

---

## 8. `department_code` como clave geográfica universal

Todo el pipeline — desde el parseo de entrada hasta la ejecución de los algoritmos y los artefactos de salida — utiliza `department_code` (un string DANE de Colombia) como clave geográfica de enrutamiento. No existe ninguna capa de abstracción (por ejemplo, `region_code` o `market_id`). Todos los componentes que filtran por geografía lo hacen mediante `department_code`.

Ejemplos representativos:

| Archivo | Línea(s) | Rol |
|---------|----------|-----|
| `src/pipeline/filters.py` | 13 | `_filter_df_by_department_code` — función central de filtrado |
| `src/optimization/solver.py` | 140, 165, 194 | Requiere la columna `department_code` en los DataFrames de entrada |
| `src/optimization/algorithms/offline/algorithm.py` | 273 | Iteración por ciudad usando `department_code` como clave |
| `src/optimization/algorithms/insert/algorithm.py` | 251, 264, 460 | Ídem |
| `src/optimization/algorithms/react/algorithm.py` | 306 | Ídem |

Más de 30 archivos referencian este campo a lo largo de todas las capas del sistema.

---

## 9. Ejemplo por defecto del módulo de disponibilidad apunta a Bogotá

`src/availability/__init__.py` línea 22:

```python
department_code="25",  # ejemplo/test por defecto del módulo
```

---

## Resumen

| Categoría | Archivos afectados | Alcance del cambio para generalizar |
|-----------|------------------|-------------------------------------|
| Zona horaria | ~12 archivos | Reemplazar `"America/Bogota"` por un parámetro de zona horaria configurable |
| Códigos de departamento e iteraciones | `solver_settings.py`, `domain.py` | Reemplazar el diccionario estático por configuración por región |
| Pico y Placa | `pico_placa.py`, `pico_y_placa.json`, `availability/__init__.py` | Subsistema 100% colombiano; requiere un interruptor por mercado |
| Parámetros de tiempo (nombres en español) | `model_params.py` | Renombrar y hacer configurable por mercado |
| Velocidades por defecto | `model_params.py` | Hacer configurable por mercado |
| Nomenclatura de funciones datetime | `datetime_utils.py` y callers | Renombrar funciones; hacer inyectable la zona horaria |
| Regla de 2 horas de anticipación | `domain.py` | Hacer configurable |
| `department_code` como clave geográfica | +30 archivos en todas las capas | Requiere refactor de abstracción geográfica |
| Tablas de datos maestros | `data/master_data/` | Se necesitan tablas equivalentes para cada nuevo mercado |
