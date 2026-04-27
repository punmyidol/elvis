# chatbot/scripts/generate_greet.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.scheduler import _plan_my_day
_plan_my_day()
print("greet.txt updated.")