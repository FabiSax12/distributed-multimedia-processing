# Manual de usuario

Este manual explica cómo usar el panel de la plataforma para enviar casos, seguir su avance y
descargar sus resultados. La instalación y el despliegue están en el [README](../README.md).

## Conceptos

- **Caso.** Un grupo de uno o varios archivos de video, audio o imagen que se envía como una sola
  solicitud.
- **Sub-tarea.** Una operación sobre un archivo del caso. Un video genera 3 sub-tareas, un audio 2
  y una imagen 1, si se dejan las operaciones automáticas.
- **Worker.** Una máquina que ejecuta sub-tareas. Hay un pool de video, uno de audio y uno de
  metadatos.
- **Reporte consolidado.** El resumen que se genera cuando terminan todas las sub-tareas del caso.

## Acceso al sistema

El sistema se usa desde el navegador en <https://dydlv3ysi1cxu.cloudfront.net/>, sin instalar nada
ni iniciar sesión. El panel tiene tres vistas en la barra superior, que son Sistema, Casos y Nuevo
caso. En la esquina superior derecha hay un indicador de conexión que debe decir
"En vivo · WebSocket".

## Envío de un caso

Los casos se crean en la vista Nuevo caso, que tiene tres pestañas.

### Desde el dataset

Sirve para armar un caso con archivos que ya están en el bucket.

1. Escribir un prefijo, por ejemplo `lote-01/`, o dejarlo vacío para ver todo el bucket, y pulsar
   Listar.
2. Marcar los archivos en la tabla.
3. Para cada archivo, elegir las operaciones o dejarlo en automático para que el coordinador use
   las de su tipo.
4. Escribir una etiqueta opcional y elegir la prioridad Normal o Alta.
5. Pulsar Enviar caso. El panel abre el caso recién creado.

### Subir archivos

Sirve para enviar archivos desde la computadora.

1. Arrastrar los archivos a la zona de carga, hasta 200 por vez.
2. Elegir las operaciones, la etiqueta y la prioridad.
3. Pulsar Subir y enviar caso.

Los archivos van directo al almacenamiento, sin pasar por el coordinador. Al terminar aparece un
aviso con el número de sub-tareas creadas y un botón Ver caso.

### Generación automática

Sirve para crear varios casos a la vez y simular carga alta.

1. Elegir el criterio de agrupación, que puede ser por carpeta o por un campo de los metadatos,
   como `batch`.
2. Revisar la vista previa, que muestra un grupo por cada carpeta o valor del campo.
3. Confirmar. El panel crea un caso por grupo, todos al mismo tiempo.

Con el dataset de prueba, agrupar por carpeta crea 30 casos con 870 sub-tareas en total.

### Operaciones disponibles

| Tipo de archivo | Operaciones automáticas | Otras que se pueden pedir |
|---|---|---|
| Video (mp4, mkv, mov, avi, webm) | Conversión, extracción de audio y miniatura | Metadatos y clasificación |
| Audio (mp3, wav, flac, ogg, m4a, aac) | Conversión y metadatos | Letras y clasificación |
| Imagen (jpg, jpeg, png, webp) | Miniatura | Metadatos y clasificación |

Un archivo con una extensión que el sistema no reconoce se acepta, pero su sub-tarea nace como
fallida por formato no soportado y así aparece en el reporte.

## Consulta del estado de un caso

En la vista Casos se busca el caso por etiqueta o identificador y se abre. Arriba aparecen su
estado y el contador del barrier, que indica cuántas sub-tareas faltan para cerrarlo. Más abajo, la
tabla de sub-tareas muestra el estado de cada una, el worker que la ejecuta, su progreso y su
duración, y se puede filtrar por estado. Si una sub-tarea falla, debajo de ella aparecen el tipo de
error y el mensaje. La vista se actualiza sola mientras el caso está abierto.

| Estado del caso | Qué significa |
|---|---|
| En cola | El caso se registró y ningún worker ha tomado todavía una de sus sub-tareas |
| En progreso | Al menos una sub-tarea está asignada o en ejecución |
| Reintentando | Se está reintentando una sub-tarea |
| Completado | Todas las sub-tareas terminaron bien |
| Parcialmente completado | El caso terminó con al menos una sub-tarea fallida |
| Fallido | Fallaron todas las sub-tareas |
| Cancelado | Se pidió cancelar el caso y ya se resolvieron todas sus sub-tareas |

Una sub-tarea pasa por Pendiente, Asignado y En ejecución, y termina como Completado, Fallido o
Cancelado. Aparece como Reintentando cuando el sistema la entrega de nuevo porque el intento
anterior no terminó.

### Cancelar un caso

Mientras el caso no haya terminado, el botón Cancelar caso lo cancela. Las sub-tareas que no han
arrancado se descartan, y las que ya están en ejecución terminan y se registran.

## Descarga de resultados y reportes

Cuando el caso termina, en su vista aparecen dos cosas.

- **Salidas.** Debajo de cada sub-tarea completada, en la línea Salida, están los archivos que
  produjo como enlaces de descarga.
- **Reporte consolidado.** El botón Ver reporte muestra el resumen del caso, una tabla por tipo y
  operación y los workers que participaron. El enlace Descargar report.json baja el reporte
  completo.

Todos los enlaces vencen a los 15 minutos. Si una descarga falla por vencimiento, basta con volver
a pulsar el botón.

Sin el panel, lo mismo se obtiene con la API. `GET /api/cases/{id}/report` devuelve la URL del
reporte y `GET /api/cases/{id}/outputs` devuelve las URL de las salidas de cada sub-tarea.

## Uso de la vista Sistema

La vista Sistema muestra el estado general.

- **Indicadores.** Casos abiertos, sub-tareas en proceso y en espera, workers vivos, mensajes en
  colas y mensajes en la DLQ.
- **Workers.** Una tarjeta por worker con su CPU, su memoria y sus sub-tareas activas. Los
  indicadores de CPU y memoria se ponen amarillos desde 70% y rojos desde 85%. Un worker aparece
  como Caído si pasa 15 segundos sin reportar.
- **Colas.** El primer número son los mensajes visibles, que esperan a un worker. El segundo son
  los mensajes en vuelo, que un worker ya tomó y todavía no terminó. Si la DLQ tiene mensajes,
  alguna sub-tarea agotó sus reintentos.
- **Gráficos.** Los mensajes esperando por pool y la CPU promedio por pool en los últimos 30
  minutos.

Las alertas aparecen arriba de los workers. Las rojas indican que un pool no tiene workers vivos o
que hay mensajes en la DLQ. Las amarillas indican que una cola de prioridad alta tiene más de 20
mensajes esperando, que todos los workers de un pool están sobre 85% de CPU o que un worker dejó
de reportar.

## Solución de problemas comunes

| Problema | Causa y solución |
|---|---|
| El indicador dice "Respaldo · polling 2 s" | La conexión en vivo se cortó y el panel consulta por HTTP. Pulsar Reconectar. Si sigue, revisar que se entre por la dirección de CloudFront |
| "Sin conexión" o "Sin respuesta de la red" | El coordinador está caído. Revisar `sudo systemctl status dmp-coordinator` en su instancia |
| Alerta "el pool 'X' no tiene workers vivos" | El servicio del worker no está corriendo. Ejecutar `deploy/deploy-worker.sh` en esa instancia o revisar `journalctl -u dmp-worker` |
| "No existen en el dataset" al crear un caso | Algún archivo no está en el bucket. Subirlo o quitarlo del caso |
| Una sub-tarea falla con "Archivo corrupto" o "Formato no soportado" | El archivo está dañado o su extensión no se soporta. El caso sigue y cierra como parcialmente completado |
| La subida de archivos falla por CORS | El panel se abrió desde otra dirección. Usar la de CloudFront |
