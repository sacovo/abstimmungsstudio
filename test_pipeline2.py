import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from abst.models import Vorlage, GeoStand, Abstimmungstag
from abst.predict import prepare_predict_data, predict_missing_results, create_models
import numpy as np

stand = GeoStand.objects.first()
tag = Abstimmungstag.objects.filter(stand=stand, projection__isnull=False).exclude(projection='').first()
print(tag)
