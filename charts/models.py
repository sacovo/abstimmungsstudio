from django.db import models

# Create your models here.


class Collection(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)

    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    published = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["-created_at"]


class Chart(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)

    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    order = models.IntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    content = models.JSONField()

    published = models.BooleanField(default=False)

    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, related_name="charts"
    )

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["order", "-created_at"]
