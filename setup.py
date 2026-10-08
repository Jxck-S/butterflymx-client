from setuptools import setup, find_packages

setup(
    name="butterflymx-client",
    version="1.0.1",
    packages=find_packages(),
    install_requires=[
        "aiohttp",
    ],
    description="Unofficial ButterflyMX API Client",
    author="Jack Sweeney",
    url="https://github.com/Jxck-S/butterflymx-client",
    python_requires=">=3.10",
)
