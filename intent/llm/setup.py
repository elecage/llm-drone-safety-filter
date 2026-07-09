from setuptools import find_packages, setup

package_name = 'intent_llm'

setup(
    name=package_name,
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools', 'openai', 'requests'],
    zip_safe=True,
    maintainer='LLM_Drone project',
    maintainer_email='elecage@users.noreply.github.com',
    description='paper §C 5-way 의도해석기 wrapper — interface + skill catalog + classifier.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'wrapper_node = intent_llm.wrapper_node:main',
        ],
    },
)
