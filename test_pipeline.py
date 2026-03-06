import os
import django
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from abst.models import GeoStand
from abst.predict import create_models

stand = GeoStand.objects.first()
start = time.time()
print("Creating models...")
create_models(stand)
print(f"Created models in {time.time() - start:.2f}s")
