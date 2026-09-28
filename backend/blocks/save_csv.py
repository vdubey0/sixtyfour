import re
from backend.blocks.sixtyfour import cell


def valid_filename(name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_. -]*\.csv', name):
        raise ValueError('Use a CSV filename containing letters, numbers, spaces, dots, underscores or hyphens')
    return name


def save_csv(df, path):
    df.applymap(cell).to_csv(path, index=False)
    return df


def run_save(df, config, context):
    path = context.output_path(config['filename'])
    save_csv(df, path)
    context.downloads.append({'filename': config['filename'], 'url': '/api/downloads/' + context.run_id + '/' + path.name})
    return df.copy()
