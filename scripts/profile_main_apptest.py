import time
import cProfile
import pstats
import io
from streamlit.testing.v1 import AppTest
from unittest.mock import patch, MagicMock

def profile_apptest():
    print("=== PROFILING APPTEST MAIN.PY ===")
    
    # Run with standard patches to simulate test conditions or real conditions
    pr = cProfile.Profile()
    t0 = time.perf_counter()
    pr.enable()
    
    from pathlib import Path
    main_path = str(Path(__file__).resolve().parents[1] / "webui" / "Main.py")
    at = AppTest.from_file(main_path)
    at.run(timeout=30)
    
    pr.disable()
    t1 = time.perf_counter()
    total_time = t1 - t0
    print(f"Total AppTest run time: {total_time:.3f} seconds")
    
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
    ps.print_stats(15)
    print("\n--- TOP CUMULATIVE TIME FUNCTIONS ---")
    print(s.getvalue())
    
    s_tot = io.StringIO()
    ps_tot = pstats.Stats(pr, stream=s_tot).sort_stats('tottime')
    ps_tot.print_stats(15)
    print("\n--- TOP TOTAL (SELF) TIME FUNCTIONS ---")
    print(s_tot.getvalue())

if __name__ == "__main__":
    profile_apptest()
