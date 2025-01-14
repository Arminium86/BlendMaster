import re

def find_self_variables_in_file(file_path):
    with open(file_path, 'r') as file:
        content = file.read()

    # Find all occurrences of self.<variable>
    matches = re.findall(r'self\.(\w+)', content)

    # Get unique variable names prefixed with 'self.'
    unique_variables = sorted(set(f"self.{var}" for var in matches))
    return unique_variables

# Specify the script file to search
file_path = r'C:\BlendMaster\blendmaster_OOP\GUI\InitialiseGUI.py' # Replace with your file name
self_variables = find_self_variables_in_file(file_path)

# Print the results
print("Unique self variables in the file:")
for variable in self_variables:
    print(variable)
