from setuptools import setup, find_packages

setup(
    name="butterflymx-client",
    version="1.0.1",
    packages=find_packages(),
    install_requires=[
        "aiohttp",
    ],
    description="Unofficial ButterflyMX API Client",
    author="Your Name",
    author_email="your.email@example.com",
)
