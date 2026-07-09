from setuptools import find_packages, setup

setup(
    name='intent_stt',
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    install_requires=['setuptools', 'sounddevice', 'pynput', 'numpy', 'requests'],
    zip_safe=True,
    maintainer='LLM_Drone project',
    maintainer_email='elecage@users.noreply.github.com',
    description='STT host pipeline — Whisper Metal + Push-to-talk + ROS 2 bridge (ADR-0015).',
    license='Apache-2.0',
    tests_require=['pytest'],
)
