import json
import logging

import pika
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .models import Review

logger = logging.getLogger(__name__)


def _publish_to_rabbitmq(review_id: int, text: str) -> None:
    credentials = pika.PlainCredentials(settings.RABBITMQ_USER, settings.RABBITMQ_PASS)
    params = pika.ConnectionParameters(
        host=settings.RABBITMQ_HOST,
        credentials=credentials,
        heartbeat=600,
        blocked_connection_timeout=300,
    )
    connection = pika.BlockingConnection(params)
    channel = connection.channel()

    channel.queue_declare(queue=settings.RABBITMQ_QUEUE, durable=True)

    channel.basic_publish(
        exchange='',
        routing_key=settings.RABBITMQ_QUEUE,
        body=json.dumps({'id': review_id, 'text': text}),
        properties=pika.BasicProperties(delivery_mode=2),  # mesaj persistent pe disc
    )
    connection.close()


@require_http_methods(['GET'])
def dashboard(request):
    reviews = Review.objects.all()
    return render(request, 'analyzer/dashboard.html', {'reviews': reviews})


@require_http_methods(['POST'])
def submit_review(request):
    text = request.POST.get('text', '').strip()
    if not text:
        return redirect('dashboard')

    review = Review.objects.create(text=text, status=Review.Status.PENDING)

    try:
        _publish_to_rabbitmq(review.id, review.text)
    except Exception as e:
        logger.error("RabbitMQ publish failed for review #%s: %s", review.id, e)
        review.status = Review.Status.FAILED
        review.save(update_fields=['status', 'updated_at'])

    return redirect('dashboard')


@require_http_methods(['GET'])
def review_status(request, pk):
    review = get_object_or_404(Review, pk=pk)
    return JsonResponse({
        'id':        review.id,
        'status':    review.status,
        'sentiment': review.sentiment,
    })
