from django.db import models


class Review(models.Model):

    class Status(models.TextChoices):
        PENDING    = 'PENDING',    'Pending'
        PROCESSING = 'PROCESSING', 'Processing'
        PROCESSED  = 'PROCESSED',  'Processed'
        FAILED     = 'FAILED',     'Failed'

    class Sentiment(models.TextChoices):
        POSITIVE = 'POSITIVE', 'Positive'
        NEGATIVE = 'NEGATIVE', 'Negative'
        NEUTRAL  = 'NEUTRAL',  'Neutral'

    text       = models.TextField()
    status     = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    sentiment  = models.CharField(max_length=20, choices=Sentiment.choices, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Review #{self.pk} [{self.status}] — {self.text[:60]}"
