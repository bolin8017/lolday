from maldet import BaseDetector, BaseDetectorConfig


class DemoConfig(BaseDetectorConfig):
    batch_size: int = 32


class DemoDetector(BaseDetector):
    config_class = DemoConfig

    def train(self):
        ...

    def evaluate(self):
        ...

    def predict(self):
        ...
