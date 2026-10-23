from django.urls import path
from shop import views
from shop.health import health

urlpatterns = [path('api/health/', health), path('api/<str:action>/', views.dispatch)]


# Development pusher