# WorldAnvil-MD-Converter

I created this as a way to backup my WorldAnvil data in a portable format like markdown.

Should be simple to run

```
python3 ./world_anvil_converter.py -s PATH_TO_WORLD_ANVIL_BACKUP_FOLDER -o PATH_TO_OUTPUT
```

It's dependant on you exporting your world anvil articles as a zip using their export tool. You can then extract that archive into a folder and point this script to it. 

It supports

- images
- articles
- categories
- histories

It ignores

- maps
- notebooks
- subscriber_groups
- timelines