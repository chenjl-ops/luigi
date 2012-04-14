# Simple REST server that takes commands in a JSON payload
import json
import os
import re
import mimetypes
import tornado.ioloop
import tornado.web
import tornado.httpclient
import tornado.httpserver
import scheduler
import pkg_resources
import pygraphviz
from cStringIO import StringIO
from rpc import RemoteSchedulerResponder


class RPCHandler(tornado.web.RequestHandler):
    """ Handle remote scheduling calls using rpc.RemoteSchedulerResponder"""
    api = RemoteSchedulerResponder(scheduler.CentralPlannerScheduler())

    def get(self, method):
        payload = self.get_argument('data', default="{}")
        arguments = json.loads(payload)

        if hasattr(self.api, method):
            result = getattr(self.api, method)(**arguments)
            self.write({"response": result})  # wrap all json response in a dictionary
        else:
            self.send_error(400)


class VisualizeHandler(tornado.web.RequestHandler):
    @tornado.web.asynchronous
    def get(self):
        client = tornado.httpclient.AsyncHTTPClient()
        # TODO: use rpc module instead of hardcoded graph
        client.fetch("http://localhost:8082/api/graph", self.on_graph)

    def on_graph(self, graph_response):
        """ TODO: clean this up using templates """

        if graph_response.error is not None:
            print "Got error from API server"
            graph_response.rethrow()
        if graph_response.code != 200:
            print "Got response code %s from API server" % graph_response.code
            self.send_error(graph_response.code)

        # TODO: figure out the interface for this

        # TODO: if there are too many nodes, we need to prune the view
        # One idea: do a Dijkstra from all running nodes. Hide all nodes
        # with distance >= 50.
        tasks = json.loads(graph_response.body)["response"]

        graphviz = pygraphviz.AGraph(directed=True, size=12)
        n_nodes = 0
        for task, p in tasks.iteritems():
            colors = {'PENDING': ('white', 'black'),
                     'DONE': ('green', 'white'),
                     'FAILED': ('red', 'white'),
                     'RUNNING': ('blue', 'white'),
                     'BROKEN': ('orange', 'black'),  # external task, can't run
                     }
            fillcolor = colors[p['status']][0]
            fontcolor = colors[p['status']][1]
            shape = 'box'
            label = task.replace('(', '\\n(').replace(',', ',\\n')  # force GraphViz to break lines
            # TODO: if the ( or , is a part of the argument we shouldn't really break it

            # TODO: FIXME: encoding strings is not compatible with newer pygraphviz
            graphviz.add_node(task.encode('utf-8'), label=label.encode('utf-8'), style='filled', fillcolor=fillcolor, fontcolor=fontcolor, shape=shape, fontname='Helvetica', fontsize=11)
            n_nodes += 1

        for task, p in tasks.iteritems():
            for dep in p['deps']:
                graphviz.add_edge(dep, task)

        if n_nodes < 200:
            graphviz.layout('dot')
        else:
            # stupid workaround...
            graphviz.layout('fdp')

        s = StringIO()
        graphviz.draw(s, format='svg')
        s.seek(0)
        svg = s.read()
        # TODO: this code definitely should not live here:
        html_header = pkg_resources.resource_string(__name__, 'static/header.html')

        pattern = r'(<svg.*?)(<g id="graph1".*?)(</svg>)'
        mo = re.search(pattern, svg, re.S)

        self.write(''.join([html_header,
         mo.group(1),
         '<g id="viewport">',
         mo.group(2),
        '</g>',
         mo.group(3),
         "</body></html>"]))

        self.finish()


class StaticFileHandler(tornado.web.RequestHandler):
    def get(self, path):
        # TODO: this is probably not the right way to do it...
        # TODO: security
        extension = os.path.splitext(path)[1]
        if extension in mimetypes.types_map:
            self.set_header("Content-Type", mimetypes.types_map[extension])
        data = pkg_resources.resource_string(__name__, os.path.join("static", path))
        self.write(data)


def apps(debug):
    api_app = tornado.web.Application([
        (r'/api/(.*)', RPCHandler),
    ], debug=debug)

    visualizer_app = tornado.web.Application([
        (r'/static/(.*)', StaticFileHandler),
        (r'/', VisualizeHandler)
    ], debug=debug)
    return api_app, visualizer_app


def run(visualizer_processes=1):
    """ Runs one instance of the API server and <visualizer_processes> visualizer servers
    """
    import daemonizer

    api_app, visualizer_app = apps(debug=False)

    visualizer_sockets = tornado.netutil.bind_sockets(8081)

    proc = daemonizer.fork_linked_workers(1 + visualizer_processes)

    if proc == 0:  # first process is API server
        print "Launching API instance"
        api_sockets = tornado.netutil.bind_sockets(8082)
        server = tornado.httpserver.HTTPServer(api_app)
        server.add_sockets(api_sockets)
    else:
        print "Launching Visualizer instance (%d)" % proc
        server = tornado.httpserver.HTTPServer(visualizer_app)
        server.add_sockets(visualizer_sockets)

    tornado.ioloop.IOLoop.instance().start()


def run_visualizer(port):
    api_app, visualizer_app = apps(debug=True)
    visualizer_app.listen(port)
    tornado.ioloop.IOLoop.instance().start()

if __name__ == "__main__":
    run_visualizer(8083)
