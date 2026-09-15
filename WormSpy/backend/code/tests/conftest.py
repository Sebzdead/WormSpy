import os
import sys

# Ensure `import core...` resolves when pytest is run from WormSpy/backend/code
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
