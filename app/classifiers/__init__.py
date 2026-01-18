from .base import Classifier, ClassifierResponse, FolderActionResponse
from .rules import RulesClassifier
from .ollama import OllamaClassifier
from .openai import OpenAIClassifier
from .multiplexed import MultiplexedClassifier
from .mock import MockAIClassifier
from .ai_auto import create_ai_classifier

__all__ = [
    "Classifier",
    "ClassifierResponse",
    "FolderActionResponse",
    "RulesClassifier",
    "OllamaClassifier",
    "OpenAIClassifier",
    "MultiplexedClassifier",
    "MockAIClassifier",
    "create_ai_classifier",
]
