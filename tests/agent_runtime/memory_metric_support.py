"""Read one Prometheus sample with labels."""

from muad_api import render_metrics


def metric(name, **labels):
    text = render_metrics()
    return sum(
        float(line.rsplit(" ", 1)[1])
        for line in text.splitlines()
        if line.startswith(name + "{")
        and all(f'{key}="{value}"' in line for key, value in labels.items())
    )
