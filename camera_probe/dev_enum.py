from pygrabber.dshow_graph import FilterGraph
g = FilterGraph()
for i, n in enumerate(g.get_input_devices()):
    print(i, repr(n))
