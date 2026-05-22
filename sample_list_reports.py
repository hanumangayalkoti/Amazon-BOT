# coding: utf-8

"""
  Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.

  Licensed under the Apache License, Version 2.0 (the "License").
  You may not use this file except in compliance with the License.
  A copy of the License is located at

      http://www.apache.org/licenses/LICENSE-2.0

  or in the "license" file accompanying this file. This file is distributed
  on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
  express or implied. See the License for the specific language governing
  permissions and limitations under the License.
"""

"""
Sample script demonstrating how to use the CreatorsAPI Python SDK for ListReports API
ListReports operation retrieves a list of available reports for your store.
"""

import json
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from creatorsapi_python_sdk.api_client import ApiClient
from creatorsapi_python_sdk.api.default_api import DefaultApi
from creatorsapi_python_sdk.exceptions import ApiException


def list_reports():
    # Initialize API client with credential details
    api_client = ApiClient(
        credential_id="<YOUR CREDENTIAL ID>",
        credential_secret="<YOUR CREDENTIAL SECRET>",
        version="<YOUR CREDENTIAL VERSION>"
    )
    
    # Initialize API
    api = DefaultApi(api_client)

    """
    Add marketplace. For more details, refer: https://affiliate-program.amazon.com/creatorsapi/docs/en-us/api-reference/common-request-headers-and-parameters#marketplace-locale-reference
    """
    marketplace = "<YOUR MARKETPLACE>"
    
    try:
        # Call the ListReports API
        response = api.list_reports(x_marketplace=marketplace)
        
        print('API called successfully.')
        print('Complete Response:\n', json.dumps(response.to_dict() if hasattr(response, 'to_dict') else str(response), indent=2))
        
    except ApiException as exception:
        print('Error calling Creators API!')
        print(exception)
    except Exception as exception:
        print('Unexpected error:', exception)


list_reports()
