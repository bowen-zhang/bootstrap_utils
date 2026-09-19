import abc
import boto3
import logging
import time

from typing import Any


_logger = logging.getLogger(__name__)


class MetricBuilder:
    def __init__(self, metric_writer: MetricWriter):
        self._metric_writer = metric_writer

    def gauge(self, name: str, unit: str = "Count") -> Gauge:
        return Gauge(self._metric_writer, name, unit)

    def counter(self, name: str, unit: str = "Count") -> Counter:
        return Counter(self._metric_writer, name, unit)

    def latency(self, name: str) -> Latency:
        return Latency(self._metric_writer, name)


class MetricWriter(abc.ABC):
    def __init__(self, namespace: str):
        self._namespace = namespace

    @property
    def namespace(self) -> str:
        return self._namespace

    @abc.abstractmethod
    def put(self, name: str, value: int | float, unit: str, dimensions: dict[str, str] | None = None):
        raise NotImplementedError("Subclasses must implement this method")


class CloudWatchMetricWriter(MetricWriter):
    def __init__(self, namespace: str, region: str = "us-west-2"):
        super().__init__(namespace)
        self._cloudwatch = boto3.client("cloudwatch", region_name=region)

    def put(self, name: str, value: int | float, unit: str, dimensions: dict[str, str] | None = None):
        metric_data = [{
            "MetricName": name,
            "Value": value,
            "Unit": unit,
            "Dimensions": [{"Name": k, "Value": v} for k, v in (dimensions or {}).items()]
        }]
        try:
            self._cloudwatch.put_metric_data(Namespace=self.namespace, MetricData=metric_data)
        except Exception as e:
            _logger.exception(f"Failed to send metric '{name}' to CloudWatch.")



class ConsoleMetricWriter(MetricWriter):
    def __init__(self, namespace: str):
        super().__init__(namespace)
        _logger.info("Using ConsoleMetricWriter for local development")

    """No-Op client for local development to avoid AWS CloudWatch calls."""
    def put(self, name: str, value: int | float, unit: str, dimensions: dict[str, str] | None = None):
        print(
            f"[LOCAL METRIC] Name={name} | "
            f"Value={value} {unit} | "
            f"Dimensions={dimensions or {}}"
        )


class Gauge:
    def __init__(self, metric_writer: MetricWriter, name: str, unit: str = "Count"):
        self._metric_writer = metric_writer
        self._name = name
        self._unit = unit

    def update(self, value: int | float, dimensions: dict[str, str] | None = None):
        self._metric_writer.put(
            name=self._name,
            value=value,
            unit=self._unit,
            dimensions=dimensions
        )


class Counter:
    def __init__(self, metric_writer: MetricWriter, name: str, unit: str = "Count"):
        self._metric_writer = metric_writer
        self._name = name
        self._unit = unit

    def increment(self, dimensions: dict[str, str] | None = None):
        self.increase_by(1, dimensions)

    def increase_by(self, value: int, dimensions: dict[str, str] | None = None):
        self._metric_writer.put(
            name=self._name,
            value=value,
            unit=self._unit,
            dimensions=dimensions
        )


class Latency:
    def __init__(self, metric_writer: MetricWriter, name: str):
        self._metric_writer = metric_writer
        self._name = name
        self._unit = "Milliseconds"
        self._start_time = None

    def __enter__(self):
        self._start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not exc_val and self._start_time is not None:
            elapsed_time_ms = (time.time() - self._start_time) * 1000  # Convert to milliseconds
            self.record(elapsed_time_ms)


    def record(self, value: float, dimensions: dict[str, str] | None = None):
        self._metric_writer.put(
            name=self._name,
            value=value,
            unit=self._unit,
            dimensions=dimensions
        )
