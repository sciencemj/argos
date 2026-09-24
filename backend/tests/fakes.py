from argos.classifier import ClassifierError, ClassifyContext, Suggestion


class FakeClassifier:
    """Returns a fixed suggestion (or raises) and remembers what it was asked."""

    def __init__(self, suggestion: Suggestion | None = None, error: str | None = None) -> None:
        self.suggestion = suggestion
        self.error = error
        self.calls: list[tuple[str, ClassifyContext]] = []

    async def classify(self, text: str, context: ClassifyContext) -> Suggestion:
        self.calls.append((text, context))
        if self.error or self.suggestion is None:
            raise ClassifierError(self.error or "no suggestion configured")
        return self.suggestion
