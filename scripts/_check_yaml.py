import sys, yaml
d = yaml.safe_load(open(sys.argv[1], encoding='utf-8'))
print('YAML OK')
print('jobs:', list(d['jobs']))
print('build steps:', len(d['jobs']['build']['steps']))
print('last step:', d['jobs']['build']['steps'][-1]['name'])
print('machine default:', d[True]['workflow_dispatch']['inputs']['machine_config']['default'])
