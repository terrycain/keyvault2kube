import logging
from typing import cast, List, Optional

import pylogrus
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException

from keyvault2kube.secret import Secret

try:
    config.load_incluster_config()
except ConfigException:
    config.load_kube_config()


logging.setLoggerClass(pylogrus.PyLogrus)


class KubeSecretManager(object):
    def __init__(self):
        self.logger = cast(pylogrus.PyLogrus, logging.getLogger('keyvault2kube.kube'))
        self.client = client.CoreV1Api()

    def update_secrets(self, secrets: List[Secret]) -> None:
        for secret in secrets:
            for ns, secret_obj in secret.to_kubesecret():
                # Get secret if it exists

                kube_secret: Optional[client.V1Secret] = None
                try:
                    kube_secret = self.client.read_namespaced_secret(name=secret_obj.metadata.name, namespace=ns)
                except ApiException as err:
                    if err.reason == 'Not Found':
                        pass
                    else:
                        self.logger.withFields({'secret': secret_obj.metadata.name}).exception('Failed to read secret', exc_info=err)
                        continue

                if kube_secret is None:
                    # Create secret
                    try:
                        self.client.create_namespaced_secret(
                            namespace=ns,
                            body=secret_obj
                        )
                    except Exception as err:
                        self.logger.withFields({'secret': secret_obj.metadata.name}).exception('Failed to create secret', exc_info=err)
                        continue
                else:
                    # Compare secret
                    kube_secret_annotations = kube_secret.metadata.annotations or {}
                    changed = False

                    for key, value in secret.annotations.items():
                        if not key.endswith('version'):
                            continue

                        if kube_secret_annotations.get(key) != value:
                            changed = True
                            break

                    if changed:
                        try:
                            self.client.patch_namespaced_secret(
                                name=secret.k8s_secret_name,
                                namespace=ns,
                                body=secret_obj.to_dict()
                            )
                        except Exception as err:
                            self.logger.withFields({'secret': secret_obj.metadata.name}).exception('Failed to patch secret', exc_info=err)
                            continue
