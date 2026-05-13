from django.urls import path
from . import views

urlpatterns = [
    path('',                       views.index,             name='index'),
    path('api/start/',             views.start_stream,      name='start_stream'),
    path('api/stop/',              views.stop_stream,       name='stop_stream'),
    path('api/status/',            views.stream_status,     name='stream_status'),
    path('api/summary/',           views.generate_summary,  name='generate_summary'),
    path('api/extract-questions/', views.extract_questions, name='extract_questions'),
    path('api/vibe-check/',        views.vibe_check,        name='vibe_check'),
]
