import multiprocessing as mp
from .runtime import main

if __name__ == "__main__":
    mp.freeze_support()
    main()
