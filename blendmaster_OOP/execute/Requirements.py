import subprocess
import sys

class Requirements:
    
    def install_requirements(self):
        """
        Install dependencies from a requirements.txt file programmatically.
        """
        requirements_file=r"C:\BlendMaster\blendmaster_OOP\requirements.txt"

        try:
            # Run the pip install command
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "-r", requirements_file,
                "--trusted-host", "pypi.org", 
                "--trusted-host", "pypi.python.org", 
                "--trusted-host", "files.pythonhosted.org"
            ])
            print(f"Dependencies installed successfully from {requirements_file}!")
        except subprocess.CalledProcessError as e:
            print(f"Failed to install dependencies: {e}")
            sys.exit(1)
