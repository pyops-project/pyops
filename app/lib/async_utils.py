import asyncio

def run_in_executor(func, *args, **kwargs):
    """Run a synchronous function in a thread executor to avoid blocking the event loop.

    Args:
        func: The synchronous function to run.
        *args: Positional arguments for func.
        **kwargs: Keyword arguments for func.

    Returns:
        Awaitable that resolves to the function's return value.
    """
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(None, lambda: func(*args, **kwargs))
