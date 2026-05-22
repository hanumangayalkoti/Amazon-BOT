# Creators API Python SDK Example

## Prerequisites

### Python Version Support
- **Supported**: To run the SDK you need Python version 3.7 or higher.

## Setup Instructions

### 1. Install and Configure Python

For Python installation, you can download it from the official website: https://www.python.org/downloads/

```bash
# Check Python version 
python3 --version  
```

### 3. Install Dependencies
```bash
cd {path_to_dir}/creatorsapi-python-sdk
# Install package dependencies
pip3 install -r requirements.txt
```

### 4. Run Sample Code
Navigate to the examples directory to run the samples.

```bash
cd examples
```

Before running the samples, you'll need to configure your API credentials in the sample files by replacing the following placeholders:

- `<YOUR CREDENTIAL ID>` - Your API credential ID
- `<YOUR CREDENTIAL SECRET>` - Your API credential secret  
- `<YOUR CREDENTIAL VERSION>` - Your credential version (e.g., "2.1" for NA, "2.2" for EU, "2.3" for FE with Cognito; "3.1" for NA, "3.2" for EU, "3.3" for FE with LWA)
- `<YOUR MARKETPLACE>` - Your marketplace to which you want to send the request (e.g., "www.amazon.com" for US marketplace)
- `<YOUR PARTNER TAG>` - Your Partner Tag for the requested marketplace in applicable sample code snippet files

Run the following commands to run the sample files:

**Get detailed product information:**
```bash
python3 sample_get_items.py
```

**Search for products:**
```bash
python3 sample_search_items.py
```

#### Other Samples
Check the `examples` directory for additional sample files with various API operations.
