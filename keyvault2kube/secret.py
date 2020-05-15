import base64
import datetime
import json
import os
from typing import Dict, Any, Optional, Generator, Tuple

import yaml
from kubernetes.client import V1Secret, V1ObjectMeta


class Secret(object):
    ANNOTATION_LAST_UPDATED = 'keyvault2kube.terrycain.github.com/secret.{}.last_updated'
    ANNOTATION_VAULT = 'keyvault2kube.terrycain.github.com/secret.{}.vault'
    ANNOTATION_VERSION = 'keyvault2kube.terrycain.github.com/secret.{}.version'

    def __init__(self,
                 secret: str,
                 secret_version: str,
                 key_vault_secret_name: str,
                 k8s_secret_name: str,
                 key_vault: str,
                 last_updated: datetime.datetime,
                 content_type: Optional[str] = None,
                 k8s_secret_key: Optional[str] = None,
                 k8s_namespaces: Optional[str] = None,
                 k8s_type: Optional[str] = None,
                 convert: Optional[str] = None,
                 ) -> None:
        if content_type is None and k8s_secret_key is None:
            raise ValueError('content_type and k8s_secret_key cannot both be empty')

        if content_type:
            self.data = self.secret_from_content_type(secret, content_type)
        else:
            self.data = {
                k8s_secret_key: secret
            }

        self.annotations = {
            self.ANNOTATION_LAST_UPDATED.format(key_vault_secret_name): last_updated.isoformat(),
            self.ANNOTATION_VERSION.format(key_vault_secret_name): secret_version,
            self.ANNOTATION_VAULT.format(key_vault_secret_name): key_vault
        }

        self.k8s_secret_key = k8s_secret_key
        self.k8s_secret_name = k8s_secret_name
        self.k8s_namespaces = k8s_namespaces or 'default'
        self.k8s_namespaces = [x.strip() for x in set(self.k8s_namespaces.split(','))]
        self.k8s_type = k8s_type or 'Opaque'

        if convert:
            if convert == 'dockerconfigjson':
                if 'registry' not in self.data or 'username' not in self.data or 'password' not in self.data or 'email' not in self.data:
                    raise ValueError('Fields missing for dockerconfigjson conversion')
                auth = self.data['username'] + ':' + self.data['password']
                data = base64.b64encode(json.dumps({
                    self.data['registry']: {
                        'username': self.data['username'],
                        'password': self.data['password'],
                        'email': self.data['email'],
                        'auth': base64.b64encode(auth.encode()).decode()
                    }
                }).encode()).decode()
                self.data.clear()
                self.data['.dockerconfigjson'] = data
                self.k8s_type = 'kubernetes.io/dockerconfigjson'
            elif convert.startswith('file:') and convert.endswith('.yaml'):
                convert_file = convert[5:]
                if not os.path.exists(convert_file):
                    raise ValueError('Convert file does not exist')
                with open(convert_file, 'r') as fp:
                    convert_data = fp.read()

                convert_data = convert_data.format(**self.data)
                self.data.clear()
                data = yaml.safe_load(convert_data)
                self.data.update(data)
            else:
                raise ValueError(f'Unknown convert value {convert}')

        # Ensure all values are base64'd
        for key in self.data:
            try:
                base64.b64decode(self.data[key])
            except Exception:
                self.data[key] = base64.b64encode(self.data[key].encode()).decode()

    @staticmethod
    def secret_from_content_type(secret: str, content_type: str) -> Dict[str, Any]:
        if content_type == 'application/json':
            return json.loads(secret)
        elif content_type == 'text/x-yaml':
            return yaml.safe_load(secret)
        else:
            raise ValueError('Unknown content type')

    def add_key(self, secret: "Secret") -> None:
        if secret.k8s_namespaces != self.k8s_namespaces:
            raise ValueError('Incompatible namespaces')
        if secret.k8s_secret_name != self.k8s_secret_name:
            raise ValueError('Secret names do not match')
        if secret.k8s_secret_key == self.k8s_secret_key:
            raise ValueError('Secret key names match')

        self.data.update(secret.data)
        self.annotations.update(secret.annotations)

    def to_yaml(self) -> Dict[str, str]:
        base = {
            'apiVersion': 'v1',
            'data': self.data,
            'kind': 'Secret',
            'type': self.k8s_type,
            'metadata': {
                'name': self.k8s_secret_name,
                'annotations': self.annotations
            }
        }
        result = {}
        for ns in self.k8s_namespaces:
            yml = base.copy()
            yml['metadata']['namespace'] = ns
            result[ns] = yaml.safe_dump(yml).rstrip('\n')
        return result

    def to_kubesecret(self) -> Generator[Tuple[str, V1Secret], None, None]:
        for ns in self.k8s_namespaces:
            yield ns, V1Secret(
                api_version='v1',
                data=self.data,
                kind='Secret',
                type=self.k8s_type,
                metadata=V1ObjectMeta(
                    annotations=self.annotations,
                    name=self.k8s_secret_name
                )
            )
