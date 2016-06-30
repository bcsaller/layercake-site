var EntityBox = React.createClass({
    getInitialState: function() {
        return {
            q: "",
            loggedIn: false
        };
    },
    setQuery: function(query) {
        this.setState({"q": query});
    },
    render: function() {
        return (
            <div id="entity-box">
            <SearchBox setQuery={this.setQuery}/>
            <EntityCollection query={this.state.q} loggedIn={this.props.loggedIn} kind="layer" url="/api/v2/layers/"/>
            </div>
        );
    }
});

var SearchBox = React.createClass({
    handleQuery: function(e) {
        e.preventDefault();
        var q = this.refs.search.value.trim();
        if (q !== "") {
            this.props.setQuery(q); //e.target.value.trim());
            $(this.refs.searchClear).css("display", "inline-block");
        } else {
            this.props.setQuery("");
            $(this.refs.searchClear).hide();
        }
    },

    clearQuery: function() {
        this.props.setQuery("");
        this.refs.search.value = '';
        $(this.refs.searchClear).hide();
    },

    componentDidMount: function() {
        $(this.refs.searchClear).hide();
    },

    render: function() {
        return (
            <div className="row text-right">
                <form className="col-md-3" _lpchecked="1">
                    <div className="form-group is-empty">
                        <input id="search"
                         type="text"
                         ref="search"
                         className="form-control col-md-8"
                         placeholder="Search..."
                         onKeyUp={this.handleQuery}/>
                     <a className="col-md-1"
                        href="#" ref="searchClear"
                        id="search-clear"
                        onClick={this.clearQuery}><i className="material-icons">clear</i></a>
                    </div>
                </form>
            </div>
        );
    }
})

var EntityCollection = React.createClass({
    getInitialState: function() {
        return {data: []};
    },

    componentDidMount: function() {
        this.queryBackend();

    },

    componentWillReceiveProps: function(props) {
        this.queryBackend({query: props.query});
    },

    queryBackend: function(p) {
        var self = this;
        var query = this.props.query;
        if (p !== undefined) {
            query = p.query;
        }
        var data = {};
        if (query &&  query.length) {
            data['q'] = query;
        }
        $.ajax({
            url: this.props.url,
            data: data,
            dataType: 'json',
            cache: false})
        .done(function(data) {
            if (self.isMounted()) {
                self.setState({data: data});
            }
        })
        .fail(function(xhr, status, err) {
        console.error(self.props.url, status, err.toString());
    });
    },

    addNew: function(event) {
        window.location = "/editor/layers/+/";
    },

    render: function() {
        var self = this;
        var entities = this.state.data.map(function(entity, index) {
            return (
                <Entity {...entity} key={entity.id}/>
            );
        });
        var addNew = "";
        if (this.props.loggedIn === true) {
            var addURL = this.props.kind + '/+/';
            addNew = <a href={addURL}>+</a>;
        }
        return (
            <div className="entityBox" ref={this.props.kind}>
                <div className="entities container">
                   {entities}
                </div>
                <div class="row">
                    <div className="text-right col-md-12">
                        <button onClick={this.addNew} type="button" className="btn btn-fab btn-primary opensource">
                            <i className="material-icons">add</i>
                        </button>
                    </div>
                </div>

            </div>
        );
    }
});

var Entity = React.createClass({
toggleDetail: function() {
    var self = this;
    var details = $(".entity-details", this.refs.entity);
    details.slideToggle();
},

render: function() {
    var detailURL = '/' + this.props.kind + '/' + this.props.id + '/';
    return (
        <div className="entity row" ref="entity">
            <div onClick={this.toggleDetail} className="col-md-12 row entity-summary">
                <div className="col-md-2 identity"><a href={detailURL} alt={this.props.id}>{this.props.name}</a></div>
                <div className="col-md-1 repo"><a href={this.props.repo}>Repo</a></div>
                <div className="col-md-4 summary">{this.props.summary}</div>
                <div className="col-md-1 owner">{this.props.owner.join(", ")}</div>
            </div>
            <EntityDetails {...this.props}/>
        </div>
        );
}
});


var EntityDetails = React.createClass({
render: function() {
    var hidden = {display: "none"};

    return (
        <div style={hidden} className="entity-details row col-md-12">
            <div className="readme col-md-7">
                README.md
            </div>
            <div className="rules col-md-4">
                Rules/Schema
            </div>
            <EntityControls {...this.props}/>
        </div>

        );
    }
});


var EntityControls = React.createClass({
    deleteEntity: function(event) {
        event.preventDefault();
        $.ajax({
            type: "DELETE",
            url: "/api/v2/layers/" + this.props.id + "/",
            processData: false,
            complete: function(xhr, status) {
                if (status === "error") {
                    $.snackbar({content: xhr.responseText});
                } else {
                    $.snackbar({content: "Removed"});
                    //window.location = "/";
                }
            }});
    },
    render: function() {
        return (
            <div className="entity-controls row">
                <a href={"/editor/layers/" + this.props.id + "/"} className="btn"><i className="material-icons">edit</i></a>
                <a onClick={this.deleteEntity} className="btn"><i className="material-icons">delete</i></a>
                <span><code>cake layer {this.props.id}</code></span>
            </div>
        );
    }
})
