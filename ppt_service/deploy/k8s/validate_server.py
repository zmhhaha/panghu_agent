"""Render and server-dry-run candidates without creating a validation namespace."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--validation-namespace', required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    kubectl = ['kubectl', '--kubeconfig=' + args.kubeconfig]

    def run(arguments, content=None):
        result = subprocess.run(kubectl + arguments, input=content, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        return result.stdout

    # Reads existence only; never creates or changes this namespace.
    run(['get', 'namespace', args.validation_namespace, '-o', 'name'])
    summary = {'persistent_mutations': 0, 'validation_namespace': args.validation_namespace,
               'namespace_mapped_only_in_validation_copy': True, 'targets': {}}
    for target in ('base', 'migrate-job'):
        rendered = run(['kustomize', str(args.root / target)])
        (args.output / (target + '.rendered.yaml')).write_text(rendered)
        # kubectl may print multiple adjacent JSON documents, not a single List.
        raw = run(['create', '--dry-run=client', '--validate=false', '-f', '-', '-o', 'json'], rendered)
        decoder, resources = json.JSONDecoder(), []
        while raw.strip():
            raw = raw.lstrip()
            parsed, end = decoder.raw_decode(raw)
            resources.extend(parsed['items'] if parsed.get('kind') == 'List' else [parsed])
            raw = raw[end:]
        (args.output / (target + '.rendered.json')).write_text(json.dumps(resources, indent=2) + '\n')
        namespace = [r for r in resources if r['kind'] == 'Namespace']
        objects = [r for r in resources if r['kind'] != 'Namespace']
        for resource in objects:
            resource['metadata']['namespace'] = args.validation_namespace
        validation = {'apiVersion': 'v1', 'kind': 'List', 'items': objects}
        (args.output / (target + '.validation.json')).write_text(json.dumps(validation, indent=2) + '\n')
        log = ''
        for resource in namespace:
            log += run(['create', '--dry-run=server', '--validate=strict', '-f', '-'], json.dumps(resource))
        log += run(['create', '--dry-run=server', '--validate=strict', '-f', '-'], json.dumps(validation))
        (args.output / (target + '.server-dry-run.log')).write_text(log)
        summary['targets'][target] = {'rendered_objects': len(resources), 'server_dry_run_passed': True}
    (args.output / 'verification.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
