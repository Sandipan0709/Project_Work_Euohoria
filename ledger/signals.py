from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .defaults import DEFAULT_CATEGORIES
from .models import Category


def seed_default_categories(user) -> None:
    for name, flow, essential, _ in DEFAULT_CATEGORIES:
        Category.objects.get_or_create(user=user, name=name, defaults={"flow": flow, "is_essential": essential})


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_default_categories(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        seed_default_categories(instance)
