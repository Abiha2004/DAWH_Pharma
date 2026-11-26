# One image for Airflow (webserver + scheduler), the file-watcher and the ingestion API
FROM apache/airflow:2.10.3-python3.11
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
ENV PROJECT_ROOT=/opt/airflow/project \
    PYTHONPATH=/opt/airflow/project:/opt/airflow/dags
WORKDIR /opt/airflow/project
