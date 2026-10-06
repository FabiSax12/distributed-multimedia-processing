# Dataset de prueba

El dataset tiene 16 archivos base, 41.2 MB en total, tomados de dos conjuntos públicos de Kaggle.
En el bucket se guardan 30 copias, en las carpetas `lote-01` a `lote-30`, lo que da 480 archivos y
1.24 GB. Los archivos no se versionan en el repositorio, solo su manifiesto.

| Tipo | Formato | Cantidad | Tamaño total | Origen |
|---|---|---|---|---|
| Audio | mp3 | 3 | 4.4 MB | [Audio files wav/mp3](https://www.kaggle.com/datasets/jayaantanaath/randomaudio-files-wavmp3) |
| Audio | wav | 4 | 5.2 MB | [Audio files wav/mp3](https://www.kaggle.com/datasets/jayaantanaath/randomaudio-files-wavmp3) |
| Video | mp4 | 3 | 28.0 MB | [Images for testing](https://www.kaggle.com/datasets/valentynsichkar/images-for-testing) |
| Imagen | jpg, jpeg, png | 6 | 3.6 MB | [Images for testing](https://www.kaggle.com/datasets/valentynsichkar/images-for-testing) |

Los archivos base son `audio01.wav` a `audio04.wav`, `freesound-sting-78788.mp3`, `sunflower.mp3`,
`the-freesound.mp3`, `bird.mp4`, `fish.mp4`, `polar_bear.mp4`, `1.jpeg`, `cat.png`, `cat1.jpg`,
`cat2.jpg`, `horse.jpg` y `ts_to_test.jpg`.

`bird.mp4` no tiene pista de audio, así que su extracción de audio falla y el caso que lo incluye
cierra como `partially_completed`. `fish.mp4` pesa 25 MB y sirve como carga alta para el pool de
video.

## Manifiesto

[`manifest.json`](manifest.json) trae los metadatos de los 480 archivos. Cada clave es la ruta del
archivo en el bucket. El coordinador lo lee y lo devuelve junto a cada archivo en
`GET /api/dataset`, y el panel puede agrupar los archivos en casos por cualquiera de sus campos.

| Campo | Aplica a | Contenido |
|---|---|---|
| `media_type` | Todos | `video`, `audio` o `image` |
| `format` | Todos | Extensión del archivo |
| `source` | Todos | Conjunto de Kaggle de origen |
| `batch` | Todos | Carpeta del lote, de `lote-01` a `lote-30` |
| `size_bytes` | Todos | Tamaño en bytes |
| `duration_ms` | Video y audio | Duración en milisegundos |
| `width`, `height` | Video e imagen | Resolución en píxeles |
| `codec` | Video y audio | Códec de video o de audio |
| `audio_codec`, `has_audio` | Video | Códec de la pista de audio y si el video la tiene |
| `sample_rate` | Audio | Frecuencia de muestreo |

Los valores se obtuvieron con FFprobe.

## Subir el dataset

Con los 16 archivos base en una carpeta y la AWS CLI configurada.

```bash
bash dataset/subir-dataset.sh <carpeta con los archivos base> [bucket]
```

El bucket por defecto es `dmp-dev-dataset`. El script sube las 30 copias y después el manifiesto a
la raíz del bucket.

## Casos de prueba

| Caso | Tipo | Archivos | Operaciones | Sub-tareas |
|---|---|---|---|---|
| CASO-001 | Homogéneo | 7 | `audio_convert` sobre los 4 wav y los 3 mp3 | 7 |
| CASO-002 | Homogéneo | 6 | `image_thumbnail` sobre las 6 imágenes | 6 |
| CASO-003 | Homogéneo | 3 | `video_convert` a mkv sobre los 3 videos | 3 |
| CASO-004 | Heterogéneo | 16 | Operaciones por defecto de cada tipo | 29 |
| CASO-005 | Heterogéneo | 16 | `metadata` y `classify` sobre todos los archivos | 32 |
| CASO-006 | Carga | 480 | Un caso por carpeta, los 30 enviados a la vez desde la generación automática | 870 |
