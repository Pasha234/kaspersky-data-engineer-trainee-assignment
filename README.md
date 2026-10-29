## Тестовое задание (команда исследования технологий машинного обучения)

В рамках задания разворачивается Inference-engine vLLM с использованием Kubernetes. Локальная проверка осуществляется через Minikube

### Предварительная настройка

Перед запуском Minikube я запускал эту команду на увеличение доступной памяти для docker-окружения Minikube, т.к. сталкивался с неизбежной нехваткой памяти после компиляции модели и загрузкой всего ЦПУ под 100% (Не знаю в чем дело)

```bash
docker update --memory=8g --memory-swap=10g minikube
```

Далее запускаем Minikube

```bash
minikube start
```

Еще короче можно через команду

```bash
minikube start --driver=docker --cpus=4 --memory=8g
```

### Docker сборка образов

Нужно собрать два образа

```bash
docker build -t model-downloader:latest downloader/
docker build -t vllm-cpu:latest .
```

Или если процессор на архитектуре Zen 4/5, можно собрать оптимизированный образ, использующий zentorch под капотом 
```bash
docker build --build-arg REQUIREMENTS_FILE=requirements-zen.txt -t vllm-zen:latest .
```

Далее загружаем образы в Minikube

```bash
minikube image load model-downloader:latest

minikube image load vllm-cpu:latest
# Или берем тот что для zen
minikube image load vllm-zen:latest
```

### Запуск кластера

Предварительно нужно задать ключ Hugging Face (опционально) и ключ для vLLM API в .env.secrets

```bash
cp k8s/base/.env.secrets.example k8s/base/.env.secrets
```

Был использован Kustomize, поэтому запуск производится через команду

```bash
kubectl apply -k k8s/base
# Или берем версию для zen
kubectl apply -k k8s/overlays/zen/
```

Далее ждем пока initContainer загружает модель. Полюбоваться загрузкой можно через логи Kubernetes

```bash
kubectl logs deployment/vllm -c model-downloader -f
```

После загрузки и предварительной подготовки vLLM приложение будет работать в штатном режиме. Статус запуска можно узнать через команду

```bash
kubectl get pods -w
```

Чтобы проверить работу сервиса, можно отправить http запрос. Для этого связываем локальный порт с портом пода

```bash
kubectl port-forward svc/vllm 8000:8000
```

Далее отправляем запрос (не забываем вставить ключ)

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "local-instruct",
    "messages": [
      {
        "role": "user",
        "content": "Hello!"
      }
    ],
    "max_tokens": 32
  }'
```

### Бенчмарк через vLLM CLI

`benchmark.sh` запускает `vllm bench serve` в образе `vllm-cpu:latest` по умолчанию. Можно указать Zen-образ,  `--image vllm-zen:latest`. Образ здесь запускает клиент бенчмарка.

Сначала нужно дождаться готовности Deployment и скопировать файлы токенизатора из контейнера `vllm` (лучшего решения не придумал). Имя работающего Pod определяется автоматически по метке `app=vllm`

```bash
kubectl rollout status deployment/vllm --timeout=20m

POD_NAME="$(kubectl get pods -l app=vllm \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.name}')"

mkdir -p ./tokenizer

kubectl cp -c vllm \
  "${POD_NAME}:/models/current/tokenizer.json" \
  ./tokenizer/tokenizer.json

kubectl cp -c vllm \
  "${POD_NAME}:/models/current/tokenizer_config.json" \
  ./tokenizer/tokenizer_config.json

kubectl cp -c vllm \
  "${POD_NAME}:/models/current/config.json" \
  ./tokenizer/config.json
```

В отдельном терминале нужно прокинуть порт к API

```bash
kubectl port-forward svc/vllm 8000:8000
```

Скрипт автоматически читает `VLLM_API_KEY` из `k8s/base/.env.secrets`.

```bash
bash benchmark.sh
```

Если файл находится в другом месте, можно задать его через аргумент `--secrets-file /path/to/.env.secrets`.

Необязательные параметры `--host` и `--port` по умолчанию равны `127.0.0.1` и `8000`. Host указывается без `http://` и без пути. Например, для другого адреса API:

```bash
bash benchmark.sh --host 192.168.1.10 --port 8080
```

Выбор Zen-образа для клиента:

```bash
bash benchmark.sh --image vllm-zen:latest
```

Параметры нагрузки задаются переменными окружения:

| Переменная | По умолчанию | Значение |
| --- | ---: | --- |
| `INPUT_LEN` | 128 | Длина случайного входа в токенах |
| `OUTPUT_LEN` | 32 | Заданный выходной бюджет токенов (`--ignore-eos`) |
| `CONCURRENCY` | 4 | Максимальное число одновременных запросов |
| `NUM_PROMPTS` | 20 | Число измеряемых запросов |
| `NUM_WARMUPS` | 2 | Число прогревочных запросов |
| `SEED` | 42 | Seed генерации случайного датасета |

Например, сравнение concurrency 1, 2 и 4 при одинаковых параметрах входа:

```bash
for concurrency in 1 2 4; do
  INPUT_LEN=100 OUTPUT_LEN=64 CONCURRENCY="$concurrency"  \
  SEED="$RANDOM" \
    bash benchmark.sh
done
```

Средние и длинные входы:

```bash
for concurrency in 1 2 4; do
  INPUT_LEN=500 OUTPUT_LEN=128 CONCURRENCY="$concurrency" SEED="$RANDOM" \
    bash benchmark.sh
done

for concurrency in 1 2; do
  INPUT_LEN=1500 OUTPUT_LEN=128 CONCURRENCY="$concurrency" SEED="$RANDOM" \
    bash benchmark.sh
done
```

Скрипт использует host драйвер для сети контейнера. Файлы токенизатора ищутся рядом со скриптом в `tokenizer/`, а результаты записываются в `bench-results/`. 

Каждый JSON получает отдельное имя с UTC-временем, длинами входа/выхода, concurrency и числом запросов.

Контейнер работает с UID/GID пользователя хоста; каталоги кеша находятся в `/tmp`, токенизатор монтируется только для чтения. Для клиентского процесса очищается `LD_PRELOAD`, унаследованный от серверного образа. Настройки сервера это не меняет. 

### Сводная таблица результатов

Все JSON-результаты `vllm bench serve` из `bench-results/` можно вывести одной
таблицей без установки дополнительных Python-пакетов:

```bash
python3 bench_summary.py
```

Таблица показывает профиль, concurrency (`C`), входные/выходные токены, модель, число успешных запросов и общее число запросов, TTFT P50, TPOT P50 и Output tok/s. Прогоны не усредняются между собой; новые файлы идут первыми по времени изменения.

Профили определяются по длине входа: Short — 100/128, Medium — 500, Long — 1500;


| Run time             | Profile | C | In/Out tok | Model          | OK/Total | TTFT P50 ms | TPOT P50 ms | Output tok/s |
| -------------------- | ------- | - | ---------- | -------------- | -------- | ----------- | ----------- | ------------ |
| 2026-10-28 22:01:30Z | Long    | 2 | 1500/128   | local-instruct | 20/20    | 2826.44     | 44.71       | 27.10        |
| 2026-10-28 21:58:46Z | Long    | 1 | 1500/128   | local-instruct | 20/20    | 2697.08     | 33.37       | 18.73        |
| 2026-10-28 21:57:29Z | Medium  | 4 | 500/128    | local-instruct | 20/20    | 6590.00     | 38.76       | 44.98        |
| 2026-10-28 21:56:13Z | Medium  | 2 | 500/128    | local-instruct | 20/20    | 894.31      | 35.83       | 44.74        |
| 2026-10-28 21:54:11Z | Medium  | 1 | 500/128    | local-instruct | 20/20    | 827.98      | 32.29       | 26.06        |
| 2026-10-28 21:53:32Z | Short   | 4 | 100/64     | local-instruct | 20/20    | 2558.82     | 34.15       | 54.64        |
| 2026-10-28 21:52:50Z | Short   | 2 | 100/64     | local-instruct | 20/20    | 243.34      | 33.07       | 53.69        |
| 2026-10-28 21:51:41Z | Short   | 1 | 100/64     | local-instruct | 20/20    | 210.65      | 32.99       | 27.87        |

### Анализ производительности и возможные оптимизации

#### А. Большой поток коротких запросов

Для большого потока коротких запросов я бы в первую очередь увеличил max-num-seqs. Также проверил бы как max-num-batched-tokens влияет на результат и возможность увеличить размер KV-cache.

#### Б. Небольшой поток запросов с большим контекстом

Здесь значительную часть вычислений занимает фаза prefill. Для оптимизации в первую очередь стоит включить chunked prefill. Также опять, если ресурсы позволяют, увеличить KV-cache

Можно проверить теорию, рассчитав необходимый KV-cache и другие параметры под точную нагрузку. Для коротких запросов возбмем размер в 100 токенов и количество в 8, 16 и 32 одновременных запросов

Результат до

| Run time             | Profile | C | In/Out tok | Model          | OK/Total | TTFT P50 ms | TPOT P50 ms | Output tok/s |
| -------------------- | ------- | - | ---------- | -------------- | -------- | ----------- | ----------- | ------------ |
| 2026-10-29 08:26:25Z | Short   | 32 | 100/64     | local-instruct | 20/20    | 10759.44    | 34.21       | 54.48 |       
| 2026-10-29 08:25:46Z | Short   | 16 | 100/64     | local-instruct | 20/20    | 10797.96    | 34.14       | 54.55 |       
| 2026-10-29 08:23:20Z | Short   | 8  | 100/64     | local-instruct | 20/20    | 7264.09     | 34.19       | 54.59 |


KV Cache Size = 2 * L * \(H_{\text{kv}}\) * D * S * B * bytes

• 2 — множитель, учитывающий хранение и ключей (Key), и значений (Value).
• L (Layers) — количество трансформерных слоев в модели.
• \(H_{\text{kv}}\) (KV Heads) — количество голов для Key/Value (с учетом Grouped-Query Attention / GQA).
• D (Head Dimension) — размерность одной головы (например, 128).
• S (Sequence Length) — длина контекста / количество токенов.
• B (Batch Size) — размер батча (количество одновременных пользователей или параллельных запросов).
• bytes — размер одного элемента в байтах (например, 2 для FP16 / BF16, 1 для FP8, 0.5 для FP4).

Подставим значения, взятые со страницы модели

KV = 2 * 24 * 2 * 896/14 * S * B * 2 = 12288 * S * B

Теперь подставим предполагаемую нагрузку

KV = 12288 * 256 * 32 = 100 663 296

Возьмем 128 Мб с запасом и получаем такой конфиг

```yaml
model: /models/current
served-model-name: local-instruct
dtype: bfloat16
max-model-len: 2048
max-num-seqs: 32
max-num-batched-tokens: 256

middleware:
  - privacy_auth.FileTokenAuthMiddleware

enable-log-requests: false
enable-log-outputs: false
disable-uvicorn-access-log: true
kv-cache-memory-bytes: 134217728 # 128 mb
```

Результаты после

| Run time             | Profile | C | In/Out tok | Model          | OK/Total | TTFT P50 ms | TPOT P50 ms | Output tok/s |
| -------------------- | ------- | - | ---------- | -------------- | -------- | ----------- | ----------- | ------------ |
| 2026-10-29 08:42:57Z | Short   | 32 | 100/64     | local-instruct | 20/20    | 2151.41     | 89.14       | 161.00 |      
| 2026-10-29 08:42:30Z | Short   | 16 | 100/64     | local-instruct | 20/20    | 1365.28     | 79.72       | 138.30 |      
| 2026-10-29 08:40:17Z | Short   | 8  | 100/64     | local-instruct | 20/20    | 755.45      | 49.53       | 121.35 | 


Теперь рассмотрим запросы с длинным контекстом в 2000 токенов.

Результаты до

| Run time             | Profile | C | In/Out tok | Model          | OK/Total | TTFT P50 ms | TPOT P50 ms | Output tok/s |
| -------------------- | ------- | - | ---------- | -------------- | -------- | ----------- | ----------- | ------------ |
|2026-10-29 08:36:28Z | Custom  | 4  | 2000/256   | local-instruct | 20/20    | 11677.28    | 237.65      | 4.97         |
|2026-10-29 08:34:51Z | Custom  | 2  | 2000/256   | local-instruct | 20/20    | 3800.77     | 133.96      | 5.03 |

Возьмкм max-num-batched-tokens размером 8096 и max-num-seqs - 2

KV = 12288 * 8096 * 2 = 198 967 296

Возьмем 256 Мб памяти под KV-cache и также добавим chunked prefill:

```yaml
model: /models/current
served-model-name: local-instruct
dtype: bfloat16
max-model-len: 2048
max-num-seqs: 2
max-num-batched-tokens: 8096

middleware:
  - privacy_auth.FileTokenAuthMiddleware

enable-log-requests: false
enable-log-outputs: false
disable-uvicorn-access-log: true
kv-cache-memory-bytes: 268435456 # 256 MiB
enable-chunked-prefill: true
```

Результаты после

| Run time             | Profile | C | In/Out tok | Model          | OK/Total | TTFT P50 ms | TPOT P50 ms | Output tok/s |
| -------------------- | ------- | - | ---------- | -------------- | -------- | ----------- | ----------- | ------------ |
|2026-10-29 08:55:36Z | Custom  | 4  | 2000/256   | local-instruct | 20/20    | 11585.35    | 236.76      | 5.01         |
|2026-10-29 08:52:15Z | Custom  | 2  | 2000/256   | local-instruct | 20/20    | 3791.28     | 41.43       | 5.03 |

